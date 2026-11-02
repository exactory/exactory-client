"""Store exact ordinary histories in authenticated, immutable artifact pages."""

import copy
import json

from .errors import ResearchError
from .evidence import digest
from .workspace import strict_json

_PAGE_MESSAGES = 32


def _require(condition, message):
    if not condition:
        raise ResearchError('review_history_invalid', message)


def _read_pages(artifacts, assignment):
    if 'history' in assignment and 'history_ref' in assignment:
        raise ResearchError('review_history_ambiguous', 'An assignment cannot carry both history and history_ref')
    root = assignment['history_ref']
    _require(isinstance(root, dict) and set(root) == {'version', 'message_count', 'history_digest', 'pages'},
             'History reference must bind its version, message count, logical digest and ordered pages')
    _require(type(root['version']) is int and root['version'] == 1
             and type(root['message_count']) is int and root['message_count'] >= 0
             and isinstance(root['history_digest'], str) and isinstance(root['pages'], list),
             'History reference version or root fields are invalid')
    _require(len(root['pages']) == (root['message_count'] + _PAGE_MESSAGES - 1) // _PAGE_MESSAGES,
             'History page count must match the bounded message count')
    history, entries = [], []
    for reference in root['pages']:
        data = artifacts.read(reference)
        try:
            page = strict_json(data)
        except ResearchError as error:
            raise ResearchError('review_history_invalid', 'History page must contain valid JSON') from error
        _require(isinstance(page, dict) and set(page) == {'version', 'start', 'messages'},
                 'History page fields are invalid')
        _require(type(page['version']) is int and page['version'] == 1
                 and type(page['start']) is int and page['start'] == len(history)
                 and isinstance(page['messages'], list)
                 and len(page['messages']) == min(_PAGE_MESSAGES, root['message_count'] - len(history)),
                 'History pages must preserve exact order and bounded message counts')
        for entry in page['messages']:
            _require(isinstance(entry, dict) and set(entry) == {'body', 'metadata', 'source', 'occurrence'},
                     'History message must retain its body, metadata, source and occurrence')
            metadata, source, occurrence = entry['metadata'], entry['source'], entry['occurrence']
            _require(isinstance(metadata, dict) and 'content' not in metadata
                     and isinstance(metadata.get('role'), str), 'History message metadata is invalid')
            _require(isinstance(source, dict) and set(source) == {'assignment_id', 'message_index'}
                     and isinstance(source['assignment_id'], str) and bool(source['assignment_id'])
                     and type(source['message_index']) is int and source['message_index'] >= 0,
                     'History message source identity is invalid')
            _require(isinstance(occurrence, dict) and set(occurrence) == {'session_id', 'index'}
                     and occurrence['session_id'] == assignment['session_id']
                     and type(occurrence['index']) is int and occurrence['index'] == len(history),
                     'History message occurrence must retain its session and exact position')
            body = artifacts.read(entry['body'])
            media_type = entry['body']['media_type']
            _require(media_type in ('text/plain', 'application/json'), 'History body format is invalid')
            try:
                content = body.decode('utf-8')
            except UnicodeError as error:
                raise ResearchError('review_history_invalid', 'History body must contain exact UTF-8 text') from error
            if media_type == 'application/json':
                try:
                    content = strict_json(content)
                except ResearchError as error:
                    raise ResearchError('review_history_invalid', 'History attachment content must contain valid JSON') from error
                _require(isinstance(content, list), 'History attachment content must preserve its native content array')
            history.append(dict(metadata, content=content))
            entries.append(entry)
    _require(len(history) == root['message_count'] and digest(history) == root['history_digest'],
             'History reference must match the complete ordered logical history')
    return history, entries


def read_history(artifacts, assignment):
    """Materialize either legacy inline messages or the versioned ordinary reference."""
    if 'history' in assignment and 'history_ref' in assignment:
        raise ResearchError('review_history_ambiguous', 'An assignment cannot carry both history and history_ref')
    if 'history_ref' in assignment:
        return _read_pages(artifacts, assignment)[0]
    return copy.deepcopy(assignment.get('history', []))


def write_history(artifacts, assignment, history, *, previous=None):
    """Publish bodies and bounded pages, retaining identities of unchanged prior messages."""
    _require(isinstance(history, list), 'Ordinary history must be an ordered message list')
    previous_history, previous_entries = [], []
    if previous is not None:
        _require(previous['session_id'] == assignment['session_id'], 'History prefixes belong to the same session')
        if 'history_ref' in previous:
            previous_history, previous_entries = _read_pages(artifacts, previous)
        else:
            previous_history = read_history(artifacts, previous)
    entries = []
    for index, message in enumerate(history):
        _require(isinstance(message, dict) and isinstance(message.get('content'), (str, list))
                 and isinstance(message.get('role'), str), 'Ordinary history requires exact text or attachment messages')
        if index < len(previous_entries) and message == previous_history[index]:
            entries.append(previous_entries[index])
            continue
        source_id = previous['id'] if previous is not None else assignment['id']
        source = (previous_entries[index]['source'] if index < len(previous_entries) else
                  {'assignment_id': source_id, 'message_index': index})
        content = message['content']
        structured = isinstance(content, list)
        data = (json.dumps(content, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
                if structured else content).encode('utf-8')
        entry = {'body': artifacts.put(data, 'application/json' if structured else 'text/plain'),
                 'metadata': {key: value for key, value in message.items() if key != 'content'},
                 'source': source,
                 'occurrence': {'session_id': assignment['session_id'], 'index': index}}
        entries.append(entry)
    pages = []
    for start in range(0, len(entries), _PAGE_MESSAGES):
        page = {'version': 1, 'start': start, 'messages': entries[start:start + _PAGE_MESSAGES]}
        data = json.dumps(page, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        pages.append(artifacts.put(data, 'application/json'))
    return {'version': 1, 'message_count': len(history), 'history_digest': digest(history), 'pages': pages}
