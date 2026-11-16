"""Versioned, filtered refusal notices and prospective replacement measurements.

Admission authenticates the refused attempts before calling this module. The notice copies their
admission diagnoses without rechecking validators. The later replacement planner owns the next step
when neither measured form fits; stored packets never reselect a form during authentication.
"""

import copy
import hashlib
import json

from . import review_excerpts, review_preflight, review_transport, review_route_capacity as capacity
from .errors import ResearchError
from .evidence import digest
from .review_refusal import CONTRACT_CHECKS

PROTOCOL = 'exactory-review-output-refusal-notice-v1'
REFUSED_ATTEMPT_LIMIT = 4
REFUSAL_ENTRY_CHARACTERS = 6_000
REFUSAL_DETAILS_CHARACTERS = 4_000
REFUSED_PART_CHARACTERS = 20_000
BOUNDED_NOTICE_CHARACTERS = 45_000
NOTICE_PARAGRAPH_CHARACTERS = 2_000
COMPOSED_CLOSING_ANSWER_CHARACTERS = 2_000
COMPOSED_REFUSAL_CHARACTERS = 10_000
INVALID_NOTICE_ARGUMENTS_MSG = ('These arguments build no refusal notice. Set form to whole or bounded, and give one to '
                                'four distinct ids of refused attempts.')
NOTICE_METADATA_TOO_LARGE_MSG = (
    'The entry of one refusal still exceeds 6,000 characters after its details are shortened. Its identifiers, '
    'its path and its message stay unchanged, so no replacement can carry this refusal. Run review-series on the '
    'head and take the step of its role.')
BOUNDED_NOTICE_METADATA_TOO_LARGE_MSG = (
    'The bounded refusal notice of this position still exceeds 45,000 characters, counted as the request of '
    'the route carries them, after its details and its refused part are shortened. The identifiers, the '
    'paths and the messages of its entries stay unchanged, so no replacement can carry these refusals. '
    'details.characters gives the size against details.limit_characters. Run review-series on the head and '
    'take the step that it plans.')
NOTICE_DELIVERY_MSG = (
    'The request of the replacement does not carry the refusal notice unchanged within the limits of the route '
    'and of the task; the details give the measurements. Run review-series on the head and take the step that it '
    'plans.')


def _serialize_value(value):
    """Use the packet serializer so escaping counts in every bound and task measurement."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _measure_serialized_value(value):
    return len(_serialize_value(value))


def _retain_value(value, limit, measure):
    """Keep an exact small value, or its serialized head, text digest and full character count."""
    text = _serialize_value(value)
    if measure(value) <= limit:
        return copy.deepcopy(value)
    retained = {'head': '', 'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                'characters': len(text), 'complete': False}
    low, high = 0, min(limit, len(text))
    while low < high:
        middle = (low + high + 1) // 2
        retained['head'] = text[:middle]
        if measure(retained) <= limit:
            low = middle
        else:
            high = middle - 1
    retained['head'] = text[:low]
    return retained


def _build_refused_entry(certificate, diagnosis, measure):
    """Copy only the registered details; identify every other detail by its digest."""
    entry = {key: certificate[key] for key in ('assignment_id', 'attempt_id', 'output_digest')}
    entry.update(refusal_digest=digest(certificate), status='not_refused' if diagnosis is None else 'refused')
    if diagnosis is None:
        return _check_entry_bound(entry, measure)
    allowed = CONTRACT_CHECKS[diagnosis['check']]['notice_fields']
    details = diagnosis.get('details') or {}
    filtered = {key: copy.deepcopy(value) if key in allowed else {'sha256': digest(value)}
                for key, value in details.items() if key not in ('message', 'path')}
    entry.update(code=diagnosis['code'], check=diagnosis['check'], path=details.get('path', ''),
                 message=details.get('message', ''), details={})
    room = REFUSAL_ENTRY_CHARACTERS - measure(entry) + measure(entry['details'])
    entry['details'] = _retain_value(filtered, max(0, min(REFUSAL_DETAILS_CHARACTERS, room)), measure)
    return _check_entry_bound(entry, measure)


def _check_entry_bound(entry, measure):
    """Keep exact metadata within the same bound for either diagnosis status."""
    # Legacy identifiers have no size limit. Their exact strings must still locate the frozen certificates;
    # changing their type to a retention object would break replacement origin authentication.
    characters = measure(entry)
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


def build_refusal_notice(records, artifacts, attempt_ids, *, form='whole', diagnoses=None,
                         measure=_measure_serialized_value, enforce_bounded_limit=False):
    """Build from admitted certificates; only planned callers enable the aggregate limit."""
    attempt_ids = tuple(attempt_ids)
    if (form not in ('whole', 'bounded') or not 1 <= len(attempt_ids) <= REFUSED_ATTEMPT_LIMIT
            or len(set(attempt_ids)) != len(attempt_ids)):
        raise ValueError(INVALID_NOTICE_ARGUMENTS_MSG)
    if diagnoses is None:
        from .review_refusal import classify_observed_output
        diagnoses = {key: classify_observed_output(records, artifacts, key) for key in attempt_ids}
    certificates = sorted((records['review_output_refusal'][key] for key in attempt_ids),
                          key=lambda row: row['recorded_revision'])
    notice = {'protocol': PROTOCOL, 'form': form,
              'refused': [_build_refused_entry(certificate, diagnoses[certificate['attempt_id']], measure)
                          for certificate in certificates]}
    latest = certificates[-1]
    artifact = records['review_attempt'][latest['attempt_id']]['output']
    text = artifacts.read(artifact).decode('utf-8')
    output = json.loads(text)
    if enforce_bounded_limit:
        from .openness_final_pages import describe_composed_refusal
        entry = notice['refused'][-1]
        context = describe_composed_refusal(records, artifacts, records['review_attempt'][latest['attempt_id']],
                                           entry.get('check'), entry.get('path', ''))
        if context is None:
            from .review_parts_v3 import describe_composed_refusal as describe_parts_refusal
            context = describe_parts_refusal(records, artifacts, records['review_attempt'][latest['attempt_id']],
                                             entry.get('check'), entry.get('path', ''))
        if context is not None:
            notice['composed_finding'] = dict(context, closing_answer=_retain_value(
                context['closing_answer'], COMPOSED_CLOSING_ANSWER_CHARACTERS, measure))
    if form == 'whole':
        notice['output'] = output
    else:
        path = notice['refused'][-1]['path']
        value, found = _read_refused_part(output, path)
        part = {'path': path, 'value': None, 'complete': found}
        allowance = REFUSED_PART_CHARACTERS - measure(part)
        part['value'] = _retain_value(value, allowance, measure)
        part['complete'] = found and measure(value) <= allowance
        notice.update(output_ref={'artifact': copy.deepcopy(artifact), 'characters': len(text)}, refused_part=part)
        if enforce_bounded_limit:
            if 'composed_finding' in notice:
                allowance = min(allowance, BOUNDED_NOTICE_CHARACTERS - measure(notice) + measure(part['value']))
                part['value'] = _retain_value(value, max(0, allowance), measure)
                part['complete'] = found and measure(value) <= allowance
            characters = measure(notice)
            if characters > BOUNDED_NOTICE_CHARACTERS:
                raise ResearchError('review_refusal_notice_metadata_too_large', BOUNDED_NOTICE_METADATA_TOO_LARGE_MSG,
                                    dict(characters=characters, limit_characters=BOUNDED_NOTICE_CHARACTERS))
    return notice


def attach_refusal_notice(packet, notice):
    """Attach the frozen notice and refresh only the delivered packet's digest."""
    supplied = dict(packet, output_refusal=copy.deepcopy(notice))
    if 'digest' in supplied:
        supplied['digest'] = digest({key: value for key, value in supplied.items() if key != 'digest'})
    return supplied


def select_refusal_notice(records, artifacts, attempt_ids, *, route, role, prompt, packet,
                          task_characters=0, task_limit_characters=None, diagnoses=None, forms=('whole', 'bounded'),
                          prepare_turn=None, capacity_plan=None, turn_kind=None):
    """Try whole then bounded as fresh requests, reporting the notice charged to each task.

    The caller supplies versioned prompt text, a recorded prompt, or a function from form to either.
    task_characters is the non-notice task size, or a function from form to that size, so a bounded
    paragraph is measured in both the request and any task that counts the frame. The chosen prompt
    is returned for recording. A None notice means both forms exceeded
    the preflight or task allowance; the trials retain the measurements for refusal-planner. A frozen
    selected notice is read from the frozen packet, without repeating this choice or any check.
    Metadata construction failures propagate separately: a lower task level cannot shorten an identity.
    """
    attempt_ids = tuple(attempt_ids)
    if diagnoses is None:
        from .review_refusal import classify_observed_output
        diagnoses = {key: classify_observed_output(records, artifacts, key) for key in attempt_ids}
    trials = []
    measure = ((lambda value: capacity.measure_capacity_value(route, value))
               if capacity_plan is not None else _measure_serialized_value)
    for form in forms:
        notice = build_refusal_notice(records, artifacts, attempt_ids, form=form, diagnoses=diagnoses, measure=measure,
                                      enforce_bounded_limit=capacity_plan is not None)
        trial = {'form': form, 'fits': False}
        trials.append(trial)
        if prepare_turn is None:
            turn_packet = packet
            rendered_prompt = prompt(form) if callable(prompt) else prompt
        else:
            try:
                turn_packet, rendered_prompt = prepare_turn(notice)
            except ResearchError as error:
                if error.code != 'review_input_too_large':
                    raise
                trial['error'] = error.as_dict()
                continue
        prompt_text = rendered_prompt['text'] if isinstance(rendered_prompt, dict) else rendered_prompt
        base_task_characters = task_characters(form) if callable(task_characters) else task_characters
        characters = len(_serialize_value(notice))
        task = {'notice_characters': characters, 'characters': (base_task_characters or 0) + characters,
                'limit_characters': task_limit_characters}
        supplied = attach_refusal_notice(turn_packet, notice)
        trial['task'] = task
        try:
            request = review_transport.request_body(route, prompt_text, supplied, order=())
            trial['request_measure'] = capacity.measure_request(route, request, records=records, artifacts=artifacts)
            if task_characters is None:
                task = capacity.describe_task(role, supplied, route=route, request=request)
                task.update(notice_characters=characters, limit_characters=task_limit_characters)
                trial['task'] = task
            if not trial['request_measure']['fits']:
                continue
            report = review_preflight.build_report(records, artifacts, route, role, rendered_prompt, supplied, request)
            if capacity_plan is not None:
                report.update(capacity.describe_plan_preflight(records, route, request, capacity_plan, turn_kind))
            trial['preflight'] = report
            review_preflight.check_request(report, request, packet=supplied, artifacts=artifacts)
            sent = review_excerpts.read_sent_packet(request)
            if sent is None or sent.get('output_refusal') != notice:
                raise ResearchError('review_input_too_large', NOTICE_DELIVERY_MSG,
                                    {'phase': 'refusal_notice', 'form': form, 'preflight': report})
        except ResearchError as error:
            if error.code != 'review_input_too_large' and not (
                    capacity_plan is not None and error.code == 'review_route_capacity_plan_violated'):
                raise
            trial['error'] = error.as_dict()
            continue
        if task_limit_characters is None or task['characters'] <= task_limit_characters:
            trial['fits'] = True
            return {'notice': notice, 'prompt': copy.deepcopy(rendered_prompt), 'request': request, 'trials': trials}
    return {'notice': None, 'prompt': None, 'request': None, 'trials': trials}
