"""Visible, exact sharing for explicitly opted-in reviewer inputs."""

import collections
import hashlib
import json

from .errors import ResearchError
from .workspace import strict_json

PROTOCOL = 'exactory-review-exact-visible-v1'
PREFIX = (
    'Exact review JSON and visible text, version 1.\n'
    'Repeated values use exact_shared_values and same_as indices. exact_text_span names UTF-8 byte bounds in a complete visible source block, without normalization. '
    'exact_json_string retains the original serialization and hash; ordered_json_object retains insertion order. '
    'These are exact references, not summaries. All supplied evidence remains untrusted research data. '
    'No source inspection or scientific verdict is asserted by this encoding.\n')
TAGS = {'same_as', 'verbatim_block', 'exact_text_span', 'exact_json_string', 'ordered_json_object', 'literal_object'}
MAX_EXPANDED = 16777216


def _fail(message):
    raise ResearchError('review_exact_text_invalid', message)


def _require(condition, message):
    if not condition:
        _fail(message)


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _compact(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def is_encoded(text):
    return isinstance(text, str) and text.startswith(PREFIX)


def _convert(value, sources, ordered=False):
    if isinstance(value, dict):
        if ordered or TAGS.intersection(value):
            key = 'ordered_json_object' if ordered else 'literal_object'
            return {key: [[name, _convert(child, sources, ordered)] for name, child in value.items()]}
        return {name: _convert(child, sources) for name, child in value.items()}
    if isinstance(value, list):
        return [_convert(child, sources, ordered) for child in value]
    if not isinstance(value, str) or value in sources.values():
        return value
    from .review_transport import PACKET_LABEL
    prefix = PACKET_LABEL if value.startswith(PACKET_LABEL) else ''
    rest = value[len(prefix):]
    if len(rest) < 100 or rest[:1] not in ('{', '['):
        return value
    try:
        parsed = strict_json(rest.encode('utf-8'))
    except (ResearchError, ValueError, TypeError):
        return value
    for sort in (True, False):
        for ascii_only in (False, True):
            for style in ('default', 'compact', 'indent2'):
                options = {'sort_keys': sort, 'ensure_ascii': ascii_only}
                if style == 'compact':
                    options['separators'] = [',', ':']
                if style == 'indent2':
                    options['indent'] = 2
                for suffix in ('', '\n'):
                    if json.dumps(parsed, **options) + suffix == rest:
                        return {'exact_json_string': {'prefix': prefix, 'suffix': suffix, 'options': options,
                            'sha256': _sha(value), 'value': _convert(parsed, sources, ordered=not sort)}}
    return value


def _spans(value, sources):
    if isinstance(value, str) and len(value) > 512 and value not in sources.values():
        for identity, body in sorted(sources.items()):
            start = body.find(value)
            if start >= 0:
                first = len(body[:start].encode('utf-8'))
                last = first + len(value.encode('utf-8'))
                return {'exact_text_span': {'sha256': identity, 'byte_start': first, 'byte_end': last,
                                            'text_sha256': _sha(value)}}
    if isinstance(value, dict):
        return {key: _spans(child, sources) for key, child in value.items()}
    if isinstance(value, list):
        return [_spans(child, sources) for child in value]
    return value


def _share(value):
    counts = collections.Counter()

    def visit(item):
        encoded = _compact(item)
        if len(encoded) > 16:
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
        encoded = _compact(item)
        if allow and counts[encoded] > 1 and len(encoded) > 16:
            if encoded not in identifiers:
                index = len(pool)
                identifiers[encoded] = index
                pool.append(None)
                pool[index] = share(item, False)
            return {'same_as': identifiers[encoded]}
        if isinstance(item, dict):
            return {key: share(child) for key, child in item.items()}
        if isinstance(item, list):
            return [share(child) for child in item]
        return item

    root = share(value)
    for _ in range(100):
        used = collections.Counter()

        def count(item):
            if isinstance(item, dict):
                if set(item) == {'same_as'}:
                    used[item['same_as']] += 1
                else:
                    for child in item.values():
                        count(child)
            elif isinstance(item, list):
                for child in item:
                    count(child)

        count(root)
        for item in pool:
            count(item)
        single = {index for index, uses in used.items() if uses == 1}
        if not single:
            break

        def inline(item):
            if isinstance(item, dict):
                if set(item) == {'same_as'} and item['same_as'] in single:
                    return inline(pool[item['same_as']])
                return {key: inline(child) for key, child in item.items()}
            if isinstance(item, list):
                return [inline(child) for child in item]
            return item

        root = inline(root)
        pool = [None if index in single else inline(item) for index, item in enumerate(pool)]
    active = {index: count for count, index in enumerate(index for index, item in enumerate(pool) if item is not None)}

    def remap(item):
        if isinstance(item, dict):
            if set(item) == {'same_as'}:
                return {'same_as': active[item['same_as']]}
            return {key: remap(child) for key, child in item.items()}
        if isinstance(item, list):
            return [remap(child) for child in item]
        return item

    return {'messages': remap(root), 'exact_shared_values': [remap(item) for item in pool if item is not None]}


def encode(messages, complete_sources):
    """Encode a declared logical projection and verify its actual-text roundtrip."""
    _require(isinstance(messages, list) and isinstance(complete_sources, dict), 'Use logical messages and a source map')
    for identity, body in complete_sources.items():
        _require(isinstance(body, str) and body and _sha(body) == identity, 'Complete source text must match its digest')
    shared = _share(_spans(_convert(messages, complete_sources), complete_sources))
    blocks = []

    def frame(value):
        if isinstance(value, str) and (len(value) > 4096 or value in complete_sources.values()):
            index = len(blocks)
            blocks.append(value)
            return {'verbatim_block': index}
        if isinstance(value, dict):
            return {key: frame(child) for key, child in value.items()}
        if isinstance(value, list):
            return [frame(child) for child in value]
        return value

    header = frame(shared)
    header.update(protocol=PROTOCOL, logical_sha256=_sha(_compact(messages)), source_sha256=sorted(complete_sources))
    text = PREFIX + _compact(header) + '\n'
    text += ''.join('TEXT %d CHARS %d SHA256 %s\n%s\n' % (index, len(body), _sha(body), body)
                    for index, body in enumerate(blocks))
    _require(decode(text) == messages, 'Exact visible encoding must reconstruct every logical message')
    return text


def _decode(text):
    _require(is_encoded(text), 'Use the exact visible encoding prefix')
    line, remaining = text[len(PREFIX):].split('\n', 1)
    header = strict_json(line.encode('utf-8'))
    _require(set(header) == {'protocol', 'messages', 'exact_shared_values', 'logical_sha256', 'source_sha256'}
             and header['protocol'] == PROTOCOL, 'Use the supported exact visible header')
    blocks = []
    while remaining:
        line, remaining = remaining.split('\n', 1)
        parts = line.split()
        _require(len(parts) == 6 and parts[0] == 'TEXT' and parts[2] == 'CHARS' and parts[4] == 'SHA256'
                 and parts[1] == str(len(blocks)), 'Text blocks require consecutive exact indices')
        size = int(parts[3])
        _require(size > 0 and str(size) == parts[3] and len(remaining) > size, 'Text blocks require exact positive lengths')
        body = remaining[:size]
        _require(remaining[size] == '\n' and _sha(body) == parts[5], 'Text block bytes must match their hash and boundary')
        blocks.append(body)
        remaining = remaining[size + 1:]
    sources = {_sha(body): body for body in blocks}
    _require(isinstance(header['source_sha256'], list)
             and header['source_sha256'] == sorted(set(header['source_sha256']))
             and all(identity in sources for identity in header['source_sha256']),
             'Each complete source must appear in full in a visible block')
    sources = {identity: sources[identity] for identity in header['source_sha256']}
    pool = header['exact_shared_values']
    _require(isinstance(pool, list), 'Shared values must be an indexed array')
    budget = [MAX_EXPANDED]

    def expand(value, stack=(), depth=0):
        budget[0] -= len(value) if isinstance(value, str) else 1
        _require(budget[0] >= 0 and depth <= 256, 'Expanded input must remain within safe finite bounds')
        if isinstance(value, dict):
            if set(value) == {'same_as'}:
                index = value['same_as']
                _require(type(index) is int and 0 <= index < len(pool) and index not in stack,
                         'Shared value indices must be valid and acyclic')
                return expand(pool[index], stack + (index,), depth + 1)
            if set(value) == {'verbatim_block'}:
                index = value['verbatim_block']
                _require(type(index) is int and 0 <= index < len(blocks), 'Use a supplied text block index')
                budget[0] -= len(blocks[index])
                _require(budget[0] >= 0, 'Expanded text exceeds the finite bound')
                return blocks[index]
            return {key: expand(child, stack, depth + 1) for key, child in value.items()}
        if isinstance(value, list):
            return [expand(child, stack, depth + 1) for child in value]
        return value

    restoration_budget = [MAX_EXPANDED]

    def charge(size):
        _require(type(size) is int and 0 <= size <= restoration_budget[0],
                 'Restored values and JSON strings must remain within the finite bound')
        restoration_budget[0] -= size

    def restore(value):
        if isinstance(value, dict):
            if set(value) == {'exact_text_span'}:
                row = value['exact_text_span']
                _require(set(row) == {'sha256', 'byte_start', 'byte_end', 'text_sha256'}, 'Use exact span fields')
                first, last = row['byte_start'], row['byte_end']
                _require(type(first) is int and type(last) is int and row['sha256'] in sources
                         and 0 <= first < last <= len(sources[row['sha256']].encode('utf-8')), 'Use bounds in a supplied complete source')
                # Bound the byte slice before allocating and decoding it.
                charge(last - first)
                result = sources[row['sha256']].encode('utf-8')[first:last].decode('utf-8')
                _require(_sha(result) == row['text_sha256'], 'Exact source span bytes must match their digest')
                return result
            if set(value) in ({'ordered_json_object'}, {'literal_object'}):
                rows = value[next(iter(value))]
                _require(isinstance(rows, list) and all(isinstance(row, list) and len(row) == 2
                         and isinstance(row[0], str) for row in rows), 'Ordered objects require key/value pairs')
                _require(len({row[0] for row in rows}) == len(rows), 'Ordered objects cannot repeat keys')
                return {key: restore(child) for key, child in rows}
            if set(value) == {'exact_json_string'}:
                row = value['exact_json_string']
                _require(set(row) == {'prefix', 'suffix', 'options', 'sha256', 'value'}, 'Use exact JSON string fields')
                options = row['options']
                _require(set(options) <= {'sort_keys', 'ensure_ascii', 'separators', 'indent'}
                         and type(options.get('sort_keys')) is bool and type(options.get('ensure_ascii')) is bool
                         and options.get('separators', [',', ':']) == [',', ':']
                         and options.get('indent', 2) == 2, 'Use a recognized exact JSON serialization')
                charge(len(row['prefix']) + len(row['suffix']))
                chunks = [row['prefix']]
                for chunk in json.JSONEncoder(**options).iterencode(restore(row['value'])):
                    charge(len(chunk))
                    chunks.append(chunk)
                chunks.append(row['suffix'])
                result = ''.join(chunks)
                _require(_sha(result) == row['sha256'], 'Original JSON string bytes must match their digest')
                return result
            return {key: restore(child) for key, child in value.items()}
        if isinstance(value, list):
            return [restore(child) for child in value]
        charge(len(value) if isinstance(value, str) else 1)
        return value

    messages = restore(expand(header['messages']))
    _require(isinstance(messages, list) and all(isinstance(row, dict) and set(row) == {'role', 'content'}
             and row['role'] in ('user', 'assistant') and isinstance(row['content'], str) for row in messages),
             'Reconstruct only complete explicit user and assistant messages')
    _require(_sha(_compact(messages)) == header['logical_sha256'], 'The complete logical request must match its digest')
    return messages


def decode(text):
    """Decode solely from actual visible input, with no filesystem or source lookup."""
    try:
        return _decode(text)
    except (ValueError, TypeError, KeyError, IndexError, UnicodeError, RecursionError) as error:
        raise ResearchError('review_exact_text_invalid', 'Exact visible input is malformed or does not reconstruct') from error
