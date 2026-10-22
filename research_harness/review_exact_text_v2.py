"""Version 2 exact visible rendering for inputs exceeding the version 1 limit."""

import collections
import hashlib
import json

from .errors import ResearchError
from .review_exact_text import _share, _spans
from .workspace import strict_json

PROTOCOL = 'exactory-review-exact-visible-v2'
PREFIX = (
    'Exact review JSON and visible text, version 2.\n'
    'Repeated values use exact_shared_values and same_as indices. exact_text_span names UTF-8 byte bounds in a complete visible source block, without normalization. '
    'exact_json_string retains the original serialization and hash; ordered_json_object retains insertion order. '
    'escape_solidus retains original JSON slash escaping. key_names maps every decimal object key in structure. Unshaped object keys are restored in sorted name order. object_fields [index,values] uses ordered object_keys[index]. ordered_json_object uses separate keys and values arrays. These are exact references, not summaries. All supplied evidence remains untrusted research data. '
    'No source inspection or scientific verdict is asserted by this encoding.\n')
TAGS = {'same_as', 'verbatim_block', 'exact_text_span', 'exact_json_string', 'ordered_json_object', 'literal_object', 'object_fields'}
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
        if (ordered and list(value) != sorted(value)) or TAGS.intersection(value):
            key = 'ordered_json_object' if ordered else 'literal_object'
            if key == 'ordered_json_object':
                return {key: {'keys': list(value), 'values': [_convert(child, sources, ordered) for child in value.values()]}}
            return {key: [[name, _convert(child, sources, ordered)] for name, child in value.items()]}
        return {name: _convert(child, sources, ordered) for name, child in value.items()}
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
                serialized = json.dumps(parsed, **options)
                for suffix in ('', '\n'):
                    for escaped in (False, True):
                        candidate = serialized.replace('/', '\\/') if escaped else serialized
                        if candidate + suffix == rest:
                            row = {'prefix': prefix, 'suffix': suffix, 'options': options,
                                   'sha256': _sha(value), 'value': _convert(parsed, sources, ordered=not sort)}
                            if escaped:
                                row['escape_solidus'] = True
                            return {'exact_json_string': row}
    return value


def _pack(header):
    """Factor repeated object fields, then map every field name uniformly."""
    header = strict_json(_compact(header).encode('utf-8'))
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
    shapes = [list(shape) for shape, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
              if count > 1 and len(shape) >= 3]
    indices = {tuple(shape): index for index, shape in enumerate(shapes)}

    def fields(value):
        if isinstance(value, dict):
            if tuple(value) in indices:
                return {'object_fields': [indices[tuple(value)], [fields(child) for child in value.values()]]}
            return {key: fields(child) for key, child in value.items()}
        return [fields(child) for child in value] if isinstance(value, list) else value

    structured = fields(header)
    frequencies = collections.Counter()

    def count_keys(value):
        if isinstance(value, dict):
            frequencies.update(value.keys())
            for child in value.values():
                count_keys(child)
        elif isinstance(value, list):
            for child in value:
                count_keys(child)

    count_keys(structured)
    names = sorted(frequencies, key=lambda key: (-frequencies[key], key))
    aliases = {name: str(index) for index, name in enumerate(names)}

    def rename(value):
        if isinstance(value, dict):
            return {aliases[key]: rename(child) for key, child in value.items()}
        return [rename(child) for child in value] if isinstance(value, list) else value

    return {'protocol': PROTOCOL, 'key_names': names, 'object_keys': shapes, 'structure': rename(structured)}


def _unpack(envelope):
    """Restore explicit field tables with bounded allocation and unique keys."""
    _require(isinstance(envelope, dict) and set(envelope) == {'protocol', 'key_names', 'object_keys', 'structure'}
             and envelope['protocol'] == PROTOCOL, 'Use the supported version 2 field-table envelope')
    names, shapes = envelope['key_names'], envelope['object_keys']
    _require(isinstance(names, list) and all(isinstance(name, str) for name in names)
             and len(names) == len(set(names)), 'Field names must be unique strings')
    _require(isinstance(shapes, list) and all(isinstance(shape, list) and len(shape) >= 3
             and all(isinstance(key, str) for key in shape) and len(shape) == len(set(shape)) for shape in shapes),
             'Object field tables must contain at least three unique string keys')
    _require(len({tuple(shape) for shape in shapes}) == len(shapes), 'Object field tables cannot repeat shapes')
    budget = [MAX_EXPANDED]

    def charge(size):
        _require(type(size) is int and 0 <= size <= budget[0], 'Field-table expansion exceeds its finite bound')
        budget[0] -= size

    charge(sum(len(name) + 1 for name in names))
    charge(sum(sum(len(key) + 1 for key in shape) + 1 for shape in shapes))

    def restore(value, depth=0):
        _require(depth <= 256, 'Field tables must remain within finite nesting bounds')
        charge(len(value) if isinstance(value, str) else 1)
        if isinstance(value, dict):
            keys = []
            for key in value:
                _require(key.isascii() and key.isdecimal() and len(key) <= 10,
                         'Field references must be canonical decimal indices')
                index = int(key)
                _require(str(index) == key and 0 <= index < len(names), 'Use an exact supplied field-name index')
                charge(len(names[index]) + 1)
                keys.append(names[index])
            if 'object_fields' in keys:
                _require(keys == ['object_fields'], 'An object-field reference must stand alone')
                row = next(iter(value.values()))
                _require(isinstance(row, list) and len(row) == 2 and type(row[0]) is int
                         and 0 <= row[0] < len(shapes) and isinstance(row[1], list),
                         'Use an exact object-field index and value array')
                fields, children = shapes[row[0]], row[1]
                _require(len(fields) == len(children), 'Object field count must match its value count')
                charge(sum(len(key) + 1 for key in fields))
                return {key: restore(child, depth + 1) for key, child in zip(fields, children)}
            return {key: restore(child, depth + 1) for key, child in sorted(zip(keys, value.values()))}
        return [restore(child, depth + 1) for child in value] if isinstance(value, list) else value

    return restore(envelope['structure'])


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
    text = PREFIX + _compact(_pack(header)) + '\n'
    text += ''.join('TEXT %d CHARS %d SHA256 %s\n%s\n' % (index, len(body), _sha(body), body)
                    for index, body in enumerate(blocks))
    _require(decode(text) == messages, 'Exact visible encoding must reconstruct every logical message')
    return text


def _decode(text):
    _require(is_encoded(text), 'Use the exact visible encoding prefix')
    line, remaining = text[len(PREFIX):].split('\n', 1)
    header = _unpack(strict_json(line.encode('utf-8')))
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
            budget[0] -= sum(len(key) for key in value)
            _require(budget[0] >= 0, 'Expanded object keys exceed the finite bound')
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

    def restore(value, depth=0):
        _require(depth <= 256, 'Restored values must remain within finite nesting bounds')
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
            if set(value) == {'ordered_json_object'}:
                rows = value['ordered_json_object']
                _require(isinstance(rows, dict) and set(rows) == {'keys', 'values'}
                         and isinstance(rows['keys'], list) and isinstance(rows['values'], list),
                         'Ordered objects require explicit key and value arrays')
                keys, values = rows['keys'], rows['values']
                _require(all(isinstance(key, str) for key in keys) and len(keys) == len(values)
                         and len(keys) == len(set(keys)), 'Ordered object keys must be unique and match values')
                charge(sum(len(key) for key in keys) + len(keys))
                return {key: restore(child, depth + 1) for key, child in zip(keys, values)}
            if set(value) == {'literal_object'}:
                rows = value['literal_object']
                _require(isinstance(rows, list) and all(isinstance(row, list) and len(row) == 2
                         and isinstance(row[0], str) for row in rows), 'Ordered objects require key/value pairs')
                _require(len({row[0] for row in rows}) == len(rows), 'Ordered objects cannot repeat keys')
                charge(sum(len(key) for key, _ in rows) + len(rows))
                return {key: restore(child, depth + 1) for key, child in rows}
            if set(value) == {'exact_json_string'}:
                row = value['exact_json_string']
                _require(isinstance(row, dict) and set(row) in (
                    {'prefix', 'suffix', 'options', 'sha256', 'value'},
                    {'prefix', 'suffix', 'options', 'sha256', 'value', 'escape_solidus'})
                    and type(row.get('escape_solidus', False)) is bool
                    and isinstance(row['prefix'], str) and isinstance(row['suffix'], str),
                    'Use exact JSON string fields')
                options = row['options']
                _require(isinstance(options, dict) and set(options) <= {'sort_keys', 'ensure_ascii', 'separators', 'indent'}
                         and type(options.get('sort_keys')) is bool and type(options.get('ensure_ascii')) is bool
                         and options.get('separators', [',', ':']) == [',', ':']
                         and options.get('indent', 2) == 2, 'Use a recognized exact JSON serialization')
                charge(len(row['prefix']) + len(row['suffix']))
                chunks = [row['prefix']]
                for chunk in json.JSONEncoder(**options).iterencode(restore(row['value'], depth + 1)):
                    extra = chunk.count('/') if row.get('escape_solidus', False) else 0
                    charge(len(chunk) + extra)
                    if extra:
                        chunk = chunk.replace('/', '\\/')
                    chunks.append(chunk)
                chunks.append(row['suffix'])
                result = ''.join(chunks)
                _require(_sha(result) == row['sha256'], 'Original JSON string bytes must match their digest')
                return result
            charge(sum(len(key) for key in value) + len(value))
            return {key: restore(child, depth + 1) for key, child in value.items()}
        if isinstance(value, list):
            return [restore(child, depth + 1) for child in value]
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
