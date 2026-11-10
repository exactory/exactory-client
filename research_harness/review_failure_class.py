"""Classify retained reviewer failures and plan bounded recoveries without writes."""

import copy
from datetime import datetime, timedelta, timezone
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import ResearchError
from .operations import normalized_text
from . import review_parts as parts
from . import review_parts_repair as repair
from . import review_protocol as protocol


# Outcome names, terminal codes and allowances have one policy home.
OUTCOME_CODES = {
    'runtime_unavailable': 'review_runtime_missing',
    'runtime_stale': 'review_route_runtime_stale',
    'format_unsupported': 'review_format_unsupported',
    'input_over_limit': 'review_input_too_large',
    'usage_limit': 'review_provider_unavailable',
    'model_at_capacity': 'review_provider_unavailable',
    'provider_unavailable': 'review_provider_unavailable',
    'provider_refused': 'review_provider_refused',
    'output_over_cap': 'review_output_over_cap',
    'execution_incomplete': 'review_execution_incomplete',
    'timeout': 'review_timeout',
    'answer_malformed': 'review_answer_malformed',
    'context_unverified': 'review_context_unverified',
    'scientific_refused': 'review_slot_exhausted',
    'unknown': 'review_failure_unknown',
}
PROVIDER_WAIT_SECONDS = (60, 120, 240, 480, 960, 1800)
_PROVIDER_OUTCOMES = ('usage_limit', 'model_at_capacity', 'provider_unavailable')
TURN_FAILURE_LIMIT = 8
SINGLE_RETRY_LIMIT = 2
CHEAP_FAILURE_SECONDS = 120
_CODEX_ADAPTERS = ('codex_cli_v1', 'codex_cli_framed_v1', 'codex_cli_visual_v1')
_MONTH_NUMBERS = {name: number for number, name in enumerate(
    ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'), 1)}
_CLAUDE_RESET = re.compile(
    r'resets ([A-Z][a-z]{2}) (\d{1,2}) at (\d{1,2})(?::(\d{2}))?(am|pm) \(([^()]+)\)')
_CODEX_RESET = re.compile(
    r'try again at ([A-Z][a-z]{2}) (\d{1,2})(?:st|nd|rd|th), (\d{4}) (\d{1,2}):(\d{2}) (AM|PM)\.',
    re.IGNORECASE)
_OUTPUT_TOKEN_MAXIMUM_ERROR = re.compile(
    r'^API Error:.*?\bresponse exceeded (?:the )?([0-9]+) output token maximum\b', re.IGNORECASE)


def read_attempt_evidence(artifacts, attempt):
    """Read retained response and answer, allowing only the reader's record errors."""
    response = {}
    if attempt.get('response') is not None:
        try:
            response = protocol._read_json(artifacts, attempt['response'])
        except (ResearchError, json.JSONDecodeError):
            response = {}
    answer = ''.join(part['text'] for item in response.get('output', []) if item.get('type') == 'message'
                     for part in item.get('content', []) if part.get('type') == 'output_text')
    return response, answer


def parse_observation_time(attempt):
    value = attempt.get('observed_at')
    if value is None:
        return None
    try:
        observed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return observed.astimezone(timezone.utc) if observed.tzinfo is not None else None


def parse_reset_time(text, observed_at):
    """Resolve a stated reset strictly after the observation, with its stated zone."""
    if observed_at is None:
        return None
    candidates = []
    for match in _CLAUDE_RESET.finditer(text):
        month, day, hour, minute, period, zone_name = match.groups()
        try:
            zone = ZoneInfo(zone_name)
        except (ZoneInfoNotFoundError, ValueError):
            continue
        local_year = observed_at.astimezone(zone).year
        for year in range(local_year, local_year + 9):
            if month not in _MONTH_NUMBERS or not 1 <= int(hour) <= 12:
                break
            try:
                candidate = datetime(year, _MONTH_NUMBERS[month], int(day),
                    int(hour) % 12 + (12 if period == 'pm' else 0), int(minute or 0), tzinfo=zone)
            except ValueError:
                continue
            future = []
            for fold in (0, 1):
                instant = candidate.replace(fold=fold).astimezone(timezone.utc)
                if (instant > observed_at
                        and instant.astimezone(zone).replace(tzinfo=None) == candidate.replace(tzinfo=None)):
                    future.append(instant)
            if future:
                candidates.extend(future)
                break
    for match in _CODEX_RESET.finditer(text):
        month, day, year, hour, minute, period = match.groups()
        if month.title() not in _MONTH_NUMBERS or not 1 <= int(hour) <= 12:
            continue
        try:
            # astimezone on a naive datetime uses the machine's local zone on this date.
            candidate = datetime(int(year), _MONTH_NUMBERS[month.title()], int(day),
                int(hour) % 12 + (12 if period.upper() == 'PM' else 0), int(minute))
        except ValueError:
            continue
        for fold in (0, 1):
            instant = candidate.replace(fold=fold).astimezone(timezone.utc)
            if instant > observed_at and instant.astimezone().replace(tzinfo=None) == candidate:
                candidates.append(instant)
    return min(candidates).isoformat(timespec='seconds') if candidates else None


def find_output_cap_signal(records, artifacts, assignment, attempt):
    """Use the adaptive handlers' own detectors before the route-token fallback."""
    route = records.get('review_route', {}).get(assignment['route_id'], {})
    maximum = route.get('configuration', {}).get('max_output_tokens')
    # Restrict the existing predicates to this exact attempt; another call cannot decide its outcome.
    evidence = dict(records, review_assignment={assignment['id']: assignment},
                    review_attempt={attempt['id']: attempt})
    packet = protocol._read_json(artifacts, assignment['packet']) if assignment.get('packet') else {}
    header = packet.get(parts.KEY, {})
    if parts.find_kind(packet) == 'read' and maximum is not None:
        detected = parts.find_failed_replacement(evidence, artifacts, assignment, header['adapter'],
            header['series_key'], assignment.get('slot_id'),
            assignment.get('context', {}).get('prior_assignment_id'), maximum)
        if detected is not None:
            return dict(source='review_parts.find_failed_replacement', value=detected)
    if repair.applies(assignment, packet) and maximum is not None:
        # Lineage and slot lookup still need all assignments, but only this call's usage.
        repair_records = dict(records, review_attempt={attempt['id']: attempt})
        slot_id = protocol.context_repair.slot_id(records, assignment)
        if repair.has_over_cap_attempt(repair_records, assignment, slot_id,
                                       packet['context_repair'].get('assignment_id')):
            return dict(source='review_parts_repair.has_over_cap_attempt', value=True)
    tokens = (attempt.get('usage') or {}).get('output_tokens')
    if type(tokens) is int and type(maximum) is int and tokens >= maximum:
        return dict(source='attempt.usage.output_tokens', value=tokens, max_output_tokens=maximum)
    return None


def classify_attempt(records, artifacts, attempt):
    """Return the first decided failure layer, or None for a usable completed output."""
    if (attempt['status'] == 'completed' and attempt.get('output') is not None
            and attempt['id'] not in records.get('review_output_refusal', {})
            and attempt.get('reservation_id') not in records.get('review_task_refusal', {})
            and protocol._context_observed(artifacts, attempt)):
        return None
    assignment = records['review_assignment'][attempt['assignment_id']]
    response, answer = read_attempt_evidence(artifacts, attempt)
    error_code = (attempt.get('error') or {}).get('code')
    diagnostic = attempt.get('provider_diagnostic') or {}
    usage_line = re.match(r"^You['’]ve (?:hit|reached) your\b[^\n]*\blimit\b", answer.strip())
    output_maximum = _OUTPUT_TOKEN_MAXIMUM_ERROR.match(answer.lstrip())
    outcome, signal, reset_at = 'unknown', dict(source='retained_evidence', value='undecided'), None
    dispatch = {'review_runtime_missing': 'runtime_unavailable',
                'review_route_runtime_stale': 'runtime_stale',
                'review_format_unsupported': 'format_unsupported', 'review_input_too_large': 'input_over_limit'}
    if error_code in dispatch:
        outcome, signal = dispatch[error_code], dict(source='attempt.error.code', value=error_code)
    elif output_maximum is not None:
        outcome = 'output_over_cap'
        signal = dict(source='response.answer', value=answer,
                      max_output_tokens=int(output_maximum.group(1)))
    elif usage_line or diagnostic.get('codex_error_info') == 'usage_limit_exceeded':
        outcome = 'usage_limit'
        signal = dict(source='response.answer' if usage_line else 'provider_diagnostic.codex_error_info',
                      value=answer if usage_line else diagnostic.get('codex_error_info'))
        reset_text = answer + '\n' + (diagnostic.get('message') or '')
        reset_at = parse_reset_time(reset_text, parse_observation_time(attempt))
    elif diagnostic.get('codex_error_info') == 'server_overloaded':
        outcome = 'model_at_capacity'
        signal = dict(source='provider_diagnostic.codex_error_info', value='server_overloaded')
    elif answer.lstrip().startswith('API Error:') or error_code == 'review_provider_failure':
        outcome = 'provider_refused' if 'safeguards' in answer.lower() else 'provider_unavailable'
        signal = dict(source='response.answer' if answer else 'attempt.error.code', value=answer or error_code)
    else:
        cap_signal = find_output_cap_signal(records, artifacts, assignment, attempt)
        execution = response.get('execution') or {}
        issues = (response.get('context_evidence') or {}).get('issues', [])
        if cap_signal is not None:
            outcome, signal = 'output_over_cap', cap_signal
        elif execution.get('timeout') is True or attempt['status'] == 'timed_out':
            outcome = 'timeout'
            signal = (dict(source='response.execution.timeout', value=True) if execution.get('timeout') is True
                      else dict(source='attempt.status', value=attempt['status']))
        elif (error_code == 'review_invocation_interrupted'
              or 'incomplete_turn' in issues or 'tool_or_injected_input' in issues
              or (bool(answer.strip()) and response.get('status') == 'incomplete')):
            outcome = 'execution_incomplete'
            if error_code == 'review_invocation_interrupted':
                signal = dict(source='attempt.error.code', value=error_code)
            elif 'incomplete_turn' in issues or 'tool_or_injected_input' in issues:
                signal = dict(source='response.context_evidence.issues', value=issues)
            else:
                signal = dict(source='response.status', value=response['status'], answer_characters=len(answer))
        elif response.get('status') == 'completed' and not attempt.get('answer_recovery'):
            try:
                protocol.transport.response_output(response)
            except (ResearchError, json.JSONDecodeError):
                outcome, signal = 'answer_malformed', dict(source='response.answer', value='not_one_object')
        if outcome == 'unknown' and attempt['status'] == 'completed':
            context = response.get('context_evidence') or {}
            refusal = records.get('review_output_refusal', {}).get(attempt['id'])
            refusal_source = 'review_output_refusal.code'
            if refusal is None:
                refusal = records.get('review_task_refusal', {}).get(attempt.get('reservation_id'))
                refusal_source = 'review_task_refusal.code'
            if context.get('inspected') is True and context.get('input_verified') is False:
                outcome = 'context_unverified'
                signal = dict(source='response.context_evidence.input_verified', value=False)
            elif refusal is not None:
                outcome = 'scientific_refused'
                signal = dict(source=refusal_source, value=refusal['code'])
    return dict(outcome=outcome, signal=signal, reset_at=reset_at)


def list_turn_assignments(records, assignment):
    """Group replacements of the same initial context or the same continuation parent."""
    parent = assignment.get('context', {}).get('prior_assignment_id')
    result = []
    for other in records.get('review_assignment', {}).values():
        if other.get('role') != assignment['role']:
            continue
        context = other.get('context', {})
        if parent is not None:
            # find_replacement groups same-parent continuations, including the
            # supported prospective change from the Codex text to visual route.
            if context.get('prior_assignment_id') == parent:
                result.append(other['id'])
            continue
        if any(other.get(key) != assignment.get(key) for key in ('route_id', 'dossier_id', 'subject')):
            continue
        if any(normalized_text(other[key]) != normalized_text(assignment[key])
               for key in ('reviewer_id', 'author_id')):
            continue
        if context == assignment.get('context', {}):
            result.append(other['id'])
    return result


def plan_output_replacement(records, artifacts, assignment):
    """Prove the existing handler produces a smaller request that passes preflight."""
    from .review_staged_protocol import ControllerArtifacts
    from . import review_preflight
    from .review_problem_parts import _measure

    packet = protocol._read_json(artifacts, assignment['packet'])
    is_read = parts.find_kind(packet) == 'read'
    is_repair = repair.applies(assignment, packet)
    if not is_read and not is_repair:
        return None
    route = records['review_route'][assignment['route_id']]
    staged = ControllerArtifacts(artifacts)
    logical = parts.read_logical_packet(staged, packet[parts.KEY]) if is_read else packet
    value = {key: copy.deepcopy(assignment[key]) for key in
             ('id', 'role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id', 'context')}
    value['id'] += '-replacement'
    if 'subject' in assignment:
        value['subject'] = copy.deepcopy(assignment['subject'])
    base = assignment['prompt']['parameters']
    if is_read:
        parameters = dict(base['base_prompt']['parameters'])
    else:
        parameters = dict(repair=bool(base.get('context_repair')),
            orchestration=base.get('orchestration'), excerpts='reviewer_excerpts' in base,
            common_version=protocol.find_common_prompt_version(assignment['prompt']))
    from .review_prompt_paragraphs import read_paragraph_parameters
    parameters.update(read_paragraph_parameters(assignment['prompt']))
    prompt = protocol.canonical_prompt(assignment['role'], **parameters)
    slot_id = protocol.context_repair.slot_id(records, assignment)
    history = protocol.read_history(staged, assignment)
    prepare = repair.prepare_packet if is_repair else parts.prepare_packet
    try:
        options = dict(base_prompt_parameters=parameters, slot_id=slot_id)
        if is_repair and 'context_repair' in value['context']:
            options['repair'] = protocol.context_repair.prepare_repair(records, staged, value)
        candidate = prepare(records, staged, value, route, logical, prompt, history, **options)
        if parts.find_kind(candidate) != 'read':
            return None
        header = candidate[parts.KEY]
        if is_read and header['unit_characters'] >= packet[parts.KEY]['unit_characters']:
            return None
        turn_prompt = parts.build_turn_prompt(assignment['role'], parameters, header)
        request = protocol.transport.request_body(route, turn_prompt['text'], candidate, [], order=())
        preflight = review_preflight.check_request(review_preflight.build_report(
            records, staged, route, assignment['role'], turn_prompt['text'], candidate, request),
            request, packet=candidate, artifacts=staged)
        old_request = protocol.build_assignment_request(records, artifacts, assignment)
        if _measure(route, request) >= _measure(route, old_request):
            return None
    except (ResearchError, json.JSONDecodeError) as error:
        if isinstance(error, ResearchError) and error.code == 'review_parts_budget_exhausted':
            return dict(error.details or {}, state='stop', code=error.code)
        return dict(state='stop', code=OUTCOME_CODES['output_over_cap'],
                    reason='replacement_inadmissible',
                    handler_error=getattr(error, 'code', 'invalid_json'))
    return dict(state='replace', code=OUTCOME_CODES['output_over_cap'],
                handler='context_repair' if is_repair else 'parts_v2_read',
                unit_characters=header['unit_characters'],
                context=copy.deepcopy(value['context']), preflight=preflight)


def plan_scheduled_retry(attempt, failure_count, reset_at=None):
    """Apply the shared delays to one allowance's own count and observation time."""
    if failure_count > len(PROVIDER_WAIT_SECONDS):
        return 'stop', None
    if reset_at is not None:
        return 'wait', reset_at
    observed_at = parse_observation_time(attempt)
    if observed_at is None:
        return 'run', None
    deadline = observed_at + timedelta(seconds=PROVIDER_WAIT_SECONDS[failure_count - 1])
    return 'wait', deadline.isoformat(timespec='seconds')


def plan_after_failure(records, artifacts, assignment_id):
    """Derive all counts and deadlines from the whole retained failure sequence."""
    assignment = records['review_assignment'][assignment_id]
    identifiers = set(list_turn_assignments(records, assignment))
    attempts = sorted((attempt for attempt in records.get('review_attempt', {}).values()
                       if attempt['assignment_id'] in identifiers), key=lambda attempt: attempt['recorded_revision'])
    failures = []
    for attempt in attempts:
        classification = classify_attempt(records, artifacts, attempt)
        if classification is None:
            failures = []
        else:
            failures.append((attempt, classification))
    if not failures:
        return dict(state='run', not_before=None, code=None, details=dict(failed_attempts=0))
    last, classification = failures[-1]
    count = len(failures)
    outcome = classification['outcome']
    code = OUTCOME_CODES[outcome]
    state, not_before = 'stop', None
    details = dict(classification, failed_attempts=count, attempt_id=last['id'])
    if outcome == 'output_over_cap':
        configuration = records['review_route'][assignment['route_id']]['configuration']
        details.update(output_tokens=(last.get('usage') or {}).get('output_tokens'),
                       max_output_tokens=configuration['max_output_tokens'])
    if count >= TURN_FAILURE_LIMIT:
        code = 'review_turn_failures_exhausted'
    elif outcome == 'output_over_cap':
        replacement = plan_output_replacement(records, artifacts, assignment)
        if replacement is not None:
            replacement = dict(replacement)
            state, code = replacement.pop('state'), replacement.pop('code')
            details.update(replacement)
    elif outcome == 'input_over_limit':
        if protocol._initial_size_failure(records, artifacts, assignment, assignment):
            state = 'replace'
            details['context'] = copy.deepcopy(assignment['context'])
    elif outcome == 'runtime_unavailable':
        runtime_count = sum(item['outcome'] == outcome for _, item in failures)
        state, not_before = plan_scheduled_retry(last, runtime_count)
    elif outcome in ('scientific_refused', 'provider_refused', 'execution_incomplete',
                     'answer_malformed', 'context_unverified'):
        outcome_count = sum(item['outcome'] == outcome for _, item in failures)
        state = 'run' if outcome_count < SINGLE_RETRY_LIMIT else 'stop'
    elif outcome in _PROVIDER_OUTCOMES or outcome == 'unknown':
        provider_count = 0
        failures_were_cheap_and_empty = True
        for attempt, item in failures:
            _, answer = read_attempt_evidence(artifacts, attempt)
            seconds = (attempt.get('usage') or {}).get('wall_seconds')
            if answer.strip() or type(seconds) not in (int, float) or not 0 <= seconds <= CHEAP_FAILURE_SECONDS:
                failures_were_cheap_and_empty = False
            route_id = records['review_assignment'][attempt['assignment_id']]['route_id']
            adapter = records['review_route'][route_id]['adapter']
            eligible = (item['outcome'] in _PROVIDER_OUTCOMES
                        or (item['outcome'] == 'unknown' and adapter in _CODEX_ADAPTERS
                            and failures_were_cheap_and_empty))
            provider_count += eligible
        if eligible:
            state, not_before = plan_scheduled_retry(last, provider_count, classification['reset_at'])
            if state == 'stop':
                code = 'review_provider_unavailable'
    if state == 'wait' and datetime.now(timezone.utc) >= datetime.fromisoformat(not_before):
        state = 'run'
    return dict(state=state, not_before=not_before, code=code, details=details)
