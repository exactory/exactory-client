"""Authenticate the narrow, lossless framing projection of completed answers."""

import hashlib
import json

from . import review_transport
from .errors import ResearchError
from .evidence import digest
from .storage import _canonical, _check_stored_bounds


PROTOCOL = 'exactory-review-answer-recovery-v1'
MAX_DEPTH = 512
JSON_WHITESPACE = ' \t\r\n'


def extract_answer_text(response):
    """Join precisely the text parts consumed by the pinned transport reader."""
    if not isinstance(response, dict) or not isinstance(response.get('output', []), list):
        return None
    parts = []
    for item in response.get('output', []):
        if not isinstance(item, dict):
            return None
        if item.get('type') != 'message':
            continue
        if not isinstance(item.get('content', []), list):
            return None
        for part in item.get('content', []):
            if not isinstance(part, dict):
                return None
            if part.get('type') == 'output_text':
                if not isinstance(part.get('text'), str):
                    return None
                parts.append(part['text'])
    return ''.join(parts)


def _scan_object(answer):
    """Find the first root boundary, bounding containers before recursive decoding."""
    start = len(answer) - len(answer.lstrip(JSON_WHITESPACE))
    if start == len(answer) or answer[start] != '{':
        raise ValueError('An answer must start with an object')
    stack = []
    in_string, escaped, has_controls = False, False, False
    for offset in range(start, len(answer)):
        character = answer[offset]
        if in_string:
            if ord(character) < 32:
                has_controls = True
            if escaped:
                escaped = False
            elif character == '\\':
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character in '{[':
            stack.append(character)
            if len(stack) > MAX_DEPTH:
                raise ValueError('The answer exceeds the nesting bound')
        elif character in '}]':
            if not stack or stack.pop() != ('{' if character == '}' else '['):
                raise ValueError('The answer has unmatched containers')
            if not stack:
                return offset + 1, has_controls
    raise ValueError('The answer is truncated')


def _decode_members(pairs):
    output = {}
    for name, value in pairs:
        if name in output:
            raise ValueError('Duplicate decoded member name')
        output[name] = value
    return output


def _refuse_constant(value):
    raise ValueError('Non-JSON constant')


def _decode_object(answer):
    """Decode a whole bounded object under the literal-control string grammar."""
    end, has_controls = _scan_object(answer)
    if answer[end:].strip(JSON_WHITESPACE):
        raise ValueError('The whole answer must be consumed')
    output = json.loads(answer, strict=False, object_pairs_hook=_decode_members,
                        parse_constant=_refuse_constant)
    # Output artifacts do not become nested record bodies. Use the grammar's
    # depth bound while retaining the store's portable integer and UTF-8 checks.
    _check_stored_bounds(output, max_depth=MAX_DEPTH)
    _canonical(output)
    return output, has_controls


def recover_answer(response):
    """Return a certified object only when the original reader fails to decode."""
    if not isinstance(response, dict) or response.get('status') != 'completed' or not response.get('id'):
        return None
    answer = extract_answer_text(response)
    if answer is None:
        return None
    try:
        review_transport.response_output(response)
    except json.JSONDecodeError as error:
        original_error = dict(kind=type(error).__name__, code='review_transport_failure')
    except (ResearchError, ValueError, RecursionError):
        return None
    else:
        return None
    try:
        end, _ = _scan_object(answer)
        suffix = answer[end:]
        rules, removed_offsets = [], []
        if not suffix.strip(JSON_WHITESPACE):
            candidate = answer
        elif all(character in JSON_WHITESPACE + '}' for character in suffix):
            rules.append('surplus_closing_braces')
            removed_offsets = [offset for offset in range(end, len(answer)) if answer[offset] == '}']
            candidate = answer[:end]
        elif suffix.lstrip(JSON_WHITESPACE).startswith(','):
            rules.append('premature_root_close')
            removed_offsets = [end - 1]
            # Validate the prefix independently too; no duplicate in its members
            # or descendants may be hidden by extending the root object.
            _decode_object(answer[:end])
            candidate = answer[:end - 1] + answer[end:]
        else:
            return None
        output, has_controls = _decode_object(candidate)
        if has_controls:
            rules.append('raw_control_characters')
        if not rules:
            return None
        certificate = dict(protocol=PROTOCOL, rules=rules, removed_offsets=removed_offsets,
                           answer_sha256=hashlib.sha256(answer.encode('utf-8')).hexdigest(),
                           output_sha256=digest(output), error=original_error)
    except (ResearchError, ValueError, UnicodeError, RecursionError):
        return None
    return dict(output=output, certificate=certificate)


def derive_output(observation, response):
    """Derive output, authenticating a recovery against the decoded stored output.

    Callers supply observation['output'] as its decoded artifact value.
    Uncertified observations retain the transport's original interpretation.
    """
    if 'answer_recovery' not in observation:
        return review_transport.response_output(response)
    try:
        recovered = recover_answer(response)
        if (recovered is not None
                and _canonical(observation['answer_recovery']) == _canonical(recovered['certificate'])
                and digest(observation.get('output')) == recovered['certificate']['output_sha256']):
            return recovered['output']
    except (ResearchError, ValueError, UnicodeError, RecursionError):
        pass
    raise ResearchError('review_answer_recovery_invalid',
                        'The retained answer, certificate and output must reproduce the exact recovery')
