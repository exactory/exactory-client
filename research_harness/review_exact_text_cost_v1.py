"""Prospective cost-aware emission using the unchanged exact-v2 grammar."""

import collections
import copy
from functools import lru_cache
import hashlib
import json
from pathlib import Path

from .errors import ResearchError
from . import review_exact_text_v2 as codec

PROTOCOL = 'exact-v2-cost-optimized-v1'
_MAX_TABLE_TRIALS = 256
_MAX_BLOCK_TRIALS = 128


def profile():
    """Bind a native prospective packet to this exact versioned emitter."""
    return {'protocol': PROTOCOL,
            'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def _require(condition, message):
    if not condition:
        raise ResearchError('review_exact_cost_invalid', message)


def _share_values(value):
    counts = collections.Counter()

    def visit(item):
        encoded = codec._compact(item)
        if len(encoded) > 8:
            counts[encoded] += 1
        if isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    pool, identifiers = [], {}

    def share(item, allow=True):
        encoded = codec._compact(item)
        if allow and counts[encoded] > 1 and len(encoded) > 8:
            if encoded not in identifiers:
                index = len(pool)
                identifiers[encoded] = index
                pool.append(None)
                pool[index] = share(item, False)
            return {'same_as': identifiers[encoded]}
        if isinstance(item, dict):
            return {key: share(child) for key, child in item.items()}
        return [share(child) for child in item] if isinstance(item, list) else item

    header = {'messages': share(value), 'exact_shared_values': pool}
    for _ in range(100):
        counts = _reference_counts(header)
        singles = {index for index, uses in counts.items() if uses == 1}
        if not singles:
            break
        header = _remap_pool(header, singles)
    return header


def _reference_counts(header):
    counts = collections.Counter()

    def visit(value):
        if isinstance(value, dict):
            if set(value) == {'same_as'}:
                counts[value['same_as']] += 1
            else:
                for child in value.values():
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(header)
    return counts


def _remap_pool(header, removed, frequency_order=False):
    pool = header['exact_shared_values']
    counts = _reference_counts(header)
    active = [index for index in range(len(pool)) if index not in removed]
    if frequency_order:
        active.sort(key=lambda index: (-counts[index], index))
    indices = {old: new for new, old in enumerate(active)}

    def remap(value):
        if isinstance(value, dict):
            if set(value) == {'same_as'}:
                index = value['same_as']
                return remap(pool[index]) if index in removed else {'same_as': indices[index]}
            return {key: remap(child) for key, child in value.items()}
        return [remap(child) for child in value] if isinstance(value, list) else value

    result = {key: remap(value) for key, value in header.items() if key != 'exact_shared_values'}
    result['exact_shared_values'] = [remap(pool[index]) for index in active]
    return result


def _node_cost(envelope, value):
    aliases = {name: str(index) for index, name in enumerate(envelope['key_names'])}
    shapes = {tuple(shape): index for index, shape in enumerate(envelope['object_keys'])}

    def convert(item):
        if isinstance(item, dict):
            if tuple(item) in shapes:
                return {aliases['object_fields']: [shapes[tuple(item)],
                                                   [convert(child) for child in item.values()]]}
            return {aliases[key]: convert(child) for key, child in item.items()}
        return [convert(child) for child in item] if isinstance(item, list) else item

    return len(codec._compact(convert(value)))


def _prune_sharing(header):
    header = json.loads(codec._compact(header))
    for _ in range(12):
        envelope, counts = codec._pack(header), _reference_counts(header)
        removed = set()
        for index, item in enumerate(header['exact_shared_values']):
            uses = counts[index]
            cost = _node_cost(envelope, item)
            reference_cost = _node_cost(envelope, {'same_as': index})
            if uses <= 1 or uses * reference_cost + cost + 1 > uses * cost:
                removed.add(index)
        if not removed:
            break
        header = json.loads(codec._compact(_remap_pool(header, removed, frequency_order=True)))
    return json.loads(codec._compact(_remap_pool(header, set(), frequency_order=True)))


def _ordered_forms(header):
    pool = header['exact_shared_values']

    def resolve(value):
        seen = set()
        while isinstance(value, dict) and set(value) == {'same_as'}:
            index = value['same_as']
            _require(type(index) is int and 0 <= index < len(pool) and index not in seen,
                     'Ordered values require valid acyclic shared references')
            seen.add(index)
            value = pool[index]
        return value

    def convert(value):
        if isinstance(value, dict):
            if set(value) == {'ordered_json_object'}:
                row = resolve(value['ordered_json_object'])
                keys = [resolve(key) for key in resolve(row['keys'])]
                values = resolve(row['values'])
                _require(len(keys) == len(values), 'Ordered object keys must match their values')
                if len(keys) <= 2:
                    return {'literal_object': [[key, convert(child)] for key, child in zip(keys, values)]}
                if not codec.TAGS.intersection(keys):
                    # Explicit field tables retain this insertion order in the unchanged decoder.
                    return {key: convert(child) for key, child in zip(keys, values)}
            return {key: convert(child) for key, child in value.items()}
        return [convert(child) for child in value] if isinstance(value, list) else value

    result = convert(header)
    for _ in range(100):
        counts = _reference_counts(result)
        removed = {index for index in range(len(result['exact_shared_values'])) if counts[index] == 0}
        if not removed:
            break
        result = _remap_pool(result, removed)
    return result


def _shape_counts(header):
    counts = collections.Counter()

    def visit(value):
        if isinstance(value, dict):
            counts[tuple(value)] += 1
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(header)
    return counts


def _pack_fields(header, selected):
    indices = {shape: index for index, shape in enumerate(selected)}

    def fields(value):
        if isinstance(value, dict):
            if tuple(value) in indices:
                return {'object_fields': [indices[tuple(value)], [fields(child) for child in value.values()]]}
            return {key: fields(child) for key, child in value.items()}
        return [fields(child) for child in value] if isinstance(value, list) else value

    structure = fields(header)
    frequencies = collections.Counter()

    def count(value):
        if isinstance(value, dict):
            frequencies.update(value.keys())
            for child in value.values():
                count(child)
        elif isinstance(value, list):
            for child in value:
                count(child)

    count(structure)
    names = sorted(frequencies, key=lambda key: (-frequencies[key], key))
    aliases = {name: str(index) for index, name in enumerate(names)}

    def rename(value):
        if isinstance(value, dict):
            return {aliases[key]: rename(child) for key, child in value.items()}
        return [rename(child) for child in value] if isinstance(value, list) else value

    return {'protocol': codec.PROTOCOL, 'key_names': names,
            'object_keys': [list(shape) for shape in selected], 'structure': rename(structure)}


def _pack_ordered(header):
    counts = _shape_counts(header)
    mandatory = {shape for shape in counts if list(shape) != sorted(shape)}
    _require(all(len(shape) >= 3 for shape in mandatory),
             'Small ordered objects require an explicit literal representation')
    selected = [shape for shape, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
                if len(shape) >= 3 and (count > 1 or shape in mandatory)]
    envelope = _pack_fields(header, selected)
    length, trials = len(codec._compact(envelope)), 0
    for _ in range(3):
        removed = 0
        for shape in list(selected):
            if shape in mandatory:
                continue
            if trials >= _MAX_TABLE_TRIALS:
                return envelope
            trials += 1
            trial = [item for item in selected if item != shape]
            proposed = _pack_fields(header, trial)
            size = len(codec._compact(proposed))
            if size < length:
                length, selected, envelope = size, trial, proposed
                removed += 1
        if not removed:
            break
    return envelope


def _render(header, blocks):
    text = codec.PREFIX + codec._compact(_pack_ordered(header)) + '\n'
    return text + ''.join('TEXT %d CHARS %d SHA256 %s\n%s\n' %
                          (index, len(body), codec._sha(body), body) for index, body in enumerate(blocks))


def _optional_blocks(header, blocks):
    selected = set(header['source_sha256'])
    identities = [codec._sha(body) for body in blocks if codec._sha(body) not in selected]
    current = _render(header, blocks)
    for identity in identities[:_MAX_BLOCK_TRIALS]:
        index = next(i for i, body in enumerate(blocks) if codec._sha(body) == identity)
        body = blocks[index]

        def inline(value):
            if isinstance(value, dict):
                if set(value) == {'verbatim_block'}:
                    old = value['verbatim_block']
                    return body if old == index else {'verbatim_block': old - 1 if old > index else old}
                return {key: inline(child) for key, child in value.items()}
            return [inline(child) for child in value] if isinstance(value, list) else value

        proposed = inline(header)
        remaining = blocks[:index] + blocks[index + 1:]
        candidate = _render(proposed, remaining)
        if len(candidate) < len(current):
            header, blocks, current = proposed, remaining, candidate
    return current, blocks


def _sources(text):
    line, remaining = text[len(codec.PREFIX):].split('\n', 1)
    header = codec._unpack(json.loads(line))
    selected = set(header['source_sha256'])
    sources = {}
    while remaining:
        line, remaining = remaining.split('\n', 1)
        size = int(line.split()[3])
        body, remaining = remaining[:size], remaining[size + 1:]
        identity = codec._sha(body)
        if identity in selected:
            sources[identity] = body
    _require(set(sources) == selected, 'Every selected complete source must remain a full visible block')
    return sources


@lru_cache(maxsize=8)
def _optimize_text(text):
    messages = codec.decode(text)
    sources = _sources(text)
    shared = _share_values(codec._spans(codec._convert(messages, sources), sources))
    blocks = []

    def frame(value):
        if isinstance(value, str) and (len(value) > 4096 or value in sources.values()):
            index = len(blocks)
            blocks.append(value)
            return {'verbatim_block': index}
        if isinstance(value, dict):
            return {key: frame(child) for key, child in value.items()}
        return [frame(child) for child in value] if isinstance(value, list) else value

    header = frame(shared)
    header.update(protocol=codec.PROTOCOL, logical_sha256=codec._sha(codec._compact(messages)),
                  source_sha256=sorted(sources))
    header = _ordered_forms(_prune_sharing(header))
    candidate, blocks = _optional_blocks(header, blocks)
    _require(codec.decode(candidate) == messages, 'Optimized text must reconstruct every original logical byte')
    _require({codec._sha(body): body for body in blocks if codec._sha(body) in sources} == sources,
             'Optimization must retain all selected complete source blocks exactly')
    return candidate if len(candidate) < len(text) else text


def optimize_request(request):
    """Return an exact request copy with only its valid v2 input text optimized."""
    _require(isinstance(request, dict), 'Use an explicit native request object')
    messages = request.get('input')
    _require(isinstance(messages, list) and len(messages) == 1 and isinstance(messages[0], dict)
             and set(messages[0]) == {'role', 'content'} and messages[0]['role'] == 'user'
             and codec.is_encoded(messages[0]['content']), 'Optimize only one complete exact-v2 user input')
    result = copy.deepcopy(request)
    result['input'][0]['content'] = _optimize_text(messages[0]['content'])
    return result
