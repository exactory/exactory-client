"""Versioned, filtered refusal notices and prospective replacement measurements.

Admission authenticates the refused attempts before calling this module. The notice copies their
stored certificates without rechecking validators. The later replacement planner owns the next step
when neither measured form fits; stored packets never reselect a form during authentication.
"""

import copy
import hashlib
import json

from . import review_excerpts, review_preflight, review_transport
from .errors import ResearchError
from .evidence import digest
from .review_refusal import CONTRACT_CHECKS

PROTOCOL = 'exactory-review-output-refusal-notice-v1'
REFUSED_ATTEMPT_LIMIT = 4
REFUSAL_ENTRY_CHARACTERS = 6_000
REFUSAL_DETAILS_CHARACTERS = 4_000
REFUSED_PART_CHARACTERS = 20_000
BOUNDED_NOTICE_CHARACTERS = 45_000
INVALID_NOTICE_ARGUMENTS_MSG = 'Choose whole or bounded and one to four distinct refused attempts.'
NOTICE_METADATA_TOO_LARGE_MSG = 'The refusal identifiers, path and message leave too little room for the bounded details.'
NOTICE_DELIVERY_MSG = 'The replacement request must carry the exact refusal notice.'


def _serialize_value(value):
    """Use the packet serializer so escaping counts in every bound and task measurement."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _retain_value(value, limit):
    """Keep an exact small value, or its serialized head, text digest and full character count."""
    text = _serialize_value(value)
    if len(text) <= limit:
        return copy.deepcopy(value)
    retained = {'head': '', 'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                'characters': len(text), 'complete': False}
    low, high = 0, min(limit, len(text))
    while low < high:
        middle = (low + high + 1) // 2
        retained['head'] = text[:middle]
        if len(_serialize_value(retained)) <= limit:
            low = middle
        else:
            high = middle - 1
    retained['head'] = text[:low]
    return retained


def _build_refused_entry(certificate):
    """Copy only the registered details; identify every other detail by its digest."""
    allowed = CONTRACT_CHECKS[certificate['check']]['notice_fields']
    details = certificate.get('details') or {}
    filtered = {key: copy.deepcopy(value) if key in allowed else {'sha256': digest(value)}
                for key, value in details.items() if key not in ('message', 'path')}
    entry = {key: certificate[key] for key in ('assignment_id', 'attempt_id', 'output_digest', 'code', 'check')}
    entry.update(refusal_digest=digest(certificate), path=details.get('path', ''),
                 message=details.get('message', ''), details={})
    room = REFUSAL_ENTRY_CHARACTERS - len(_serialize_value(entry)) + len(_serialize_value(entry['details']))
    entry['details'] = _retain_value(filtered, max(0, min(REFUSAL_DETAILS_CHARACTERS, room)))
    # Legacy identifiers have no size limit. Their exact strings must still locate the frozen certificates;
    # changing their type to a retention object would break replacement origin authentication.
    characters = len(_serialize_value(entry))
    if characters > REFUSAL_ENTRY_CHARACTERS:
        raise ResearchError('review_refusal_notice_metadata_too_large', NOTICE_METADATA_TOO_LARGE_MSG,
                            {'refusal_digest': entry['refusal_digest'], 'characters': characters,
                             'limit_characters': REFUSAL_ENTRY_CHARACTERS})
    return entry


def _read_refused_part(output, path):
    """Read a JSON pointer, including arrays; a missing refused field has no fabricated value."""
    value = output
    if path == '':
        return value, True
    if not isinstance(path, str) or not path.startswith('/'):
        return None, False
    for component in path[1:].split('/'):
        key = component.replace('~1', '/').replace('~0', '~')
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and key.isascii() and key.isdigit() and (key == '0' or not key.startswith('0')):
            # Compare as text first: an arbitrarily long pointer component need not become an integer.
            if len(key) > len(str(len(value))) or int(key) >= len(value):
                return None, False
            value = value[int(key)]
        else:
            return None, False
    return value, True


def build_refusal_notice(records, artifacts, attempt_ids, *, form='whole'):
    """Build a new notice from admitted certificates, leaving every stored byte and digest unchanged."""
    attempt_ids = tuple(attempt_ids)
    if (form not in ('whole', 'bounded') or not 1 <= len(attempt_ids) <= REFUSED_ATTEMPT_LIMIT
            or len(set(attempt_ids)) != len(attempt_ids)):
        raise ValueError(INVALID_NOTICE_ARGUMENTS_MSG)
    certificates = sorted((records['review_output_refusal'][key] for key in attempt_ids),
                          key=lambda row: row['recorded_revision'])
    notice = {'protocol': PROTOCOL, 'form': form,
              'refused': [_build_refused_entry(certificate) for certificate in certificates]}
    latest = certificates[-1]
    artifact = records['review_attempt'][latest['attempt_id']]['output']
    text = artifacts.read(artifact).decode('utf-8')
    output = json.loads(text)
    if form == 'whole':
        notice['output'] = output
    else:
        path = (latest.get('details') or {}).get('path', '')
        value, found = _read_refused_part(output, path)
        part = {'path': path, 'value': None, 'complete': found}
        allowance = REFUSED_PART_CHARACTERS - len(_serialize_value(part))
        part['value'] = _retain_value(value, allowance)
        part['complete'] = found and len(_serialize_value(value)) <= allowance
        notice.update(output_ref={'artifact': copy.deepcopy(artifact), 'characters': len(text)}, refused_part=part)
    return notice


def select_refusal_notice(records, artifacts, attempt_ids, *, route, role, prompt, packet,
                          task_characters=0, task_limit_characters=None):
    """Try whole then bounded as fresh requests, reporting the notice charged to each task.

    The caller supplies versioned prompt text, a recorded prompt, or a function from form to either.
    task_characters is the non-notice task size, or a function from form to that size, so a bounded
    paragraph is measured in both the request and any task that counts the frame. The chosen prompt
    is returned for recording. A None notice means both forms exceeded
    the preflight or task allowance; the trials retain the measurements for refusal-planner. A frozen
    selected notice is rebuilt with build_refusal_notice(form=...), without repeating this choice.
    Metadata construction failures propagate separately: a lower task level cannot shorten an identity.
    """
    attempt_ids = tuple(attempt_ids)
    trials = []
    for form in ('whole', 'bounded'):
        notice = build_refusal_notice(records, artifacts, attempt_ids, form=form)
        rendered_prompt = prompt(form) if callable(prompt) else prompt
        prompt_text = rendered_prompt['text'] if isinstance(rendered_prompt, dict) else rendered_prompt
        base_task_characters = task_characters(form) if callable(task_characters) else task_characters
        characters = len(_serialize_value(notice))
        task = {'notice_characters': characters, 'characters': base_task_characters + characters,
                'limit_characters': task_limit_characters}
        supplied = dict(packet, output_refusal=notice)
        if 'digest' in supplied:
            supplied['digest'] = digest({key: value for key, value in supplied.items() if key != 'digest'})
        trial = {'form': form, 'task': task, 'fits': False}
        trials.append(trial)
        try:
            request = review_transport.request_body(route, prompt_text, supplied)
            report = review_preflight.build_report(records, artifacts, route, role, rendered_prompt, supplied, request)
            trial['preflight'] = report
            review_preflight.check_request(report, request, packet=supplied, artifacts=artifacts)
            sent = review_excerpts.read_sent_packet(request)
            if sent is None or sent.get('output_refusal') != notice:
                raise ResearchError('review_input_too_large', NOTICE_DELIVERY_MSG,
                                    {'phase': 'refusal_notice', 'form': form, 'preflight': report})
        except ResearchError as error:
            if error.code != 'review_input_too_large':
                raise
            trial['error'] = error.as_dict()
            continue
        if task_limit_characters is None or task['characters'] <= task_limit_characters:
            trial['fits'] = True
            return {'notice': notice, 'prompt': copy.deepcopy(rendered_prompt), 'request': request, 'trials': trials}
    return {'notice': None, 'prompt': None, 'request': None, 'trials': trials}
