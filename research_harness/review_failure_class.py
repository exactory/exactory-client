"""Classify retained reviewer failures and plan bounded recoveries without writes."""

import base64
import copy
from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any, Dict, List, Literal, TypedDict, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import ResearchError
from .operations import normalized_text
from . import review_parts as parts
from . import review_parts_repair as repair
from . import review_protocol as protocol
from .review_route_capacity import plan_capacity_recovery


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
    'response_over_limit': 'review_response_too_large',
    'execution_incomplete': 'review_execution_incomplete',
    'interrupted': 'review_invocation_interrupted',
    'stopped': 'review_chain_stopped',
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
# 18 safeguards stops in 1,114 retained CLI runs across LIVE-RECORDS (2026-11-13).
# (1 - (18/1114)**3)**66 = 0.9997216, including the largest chain's planned final turn.
PROVIDER_REFUSAL_ATTEMPT_LIMIT = 3
SEQUENCE_LIMIT = 3
SEQUENCE_WAIT_SECONDS = 1800
IDENTICAL_OVER_CAP_ATTEMPT_LIMIT = 2
ROUTE_DECISION_QUESTION = 'Whether this slot or chain moves to a route with another effort or limit.'
# c bounds attempts at different task levels only (ruling 19). Independence is
# an assumption, not a measured property. Test it by grouping retained retries
# by task level and hour, then comparing P(over cap | earlier over cap) with p.
# Provider safeguards have the same assumption; group W4's sample by hour.
OUTCOME_ROWS = {
    name: dict(attempt_limit=limit, exhausted=exhausted, schedule=schedule)
    for name, limit, exhausted, schedule in (
        ('usage_limit', 7, 'sequence', 'provider'),
        ('model_at_capacity', 7, 'sequence', 'provider'),
        ('provider_unavailable', 7, 'sequence', 'provider'),
        ('runtime_unavailable', 7, 'handoff', 'runtime'),
        ('runtime_stale', 1, 'handoff', None),
        ('provider_refused', PROVIDER_REFUSAL_ATTEMPT_LIMIT, 'sequence', None),
        ('output_over_cap', IDENTICAL_OVER_CAP_ATTEMPT_LIMIT, 'decision', None),
        ('response_over_limit', 2, 'decision', None),
        ('input_over_limit', 1, 'decision', None),
        ('timeout', 2, 'decision', None),
        ('execution_incomplete', 3, 'sequence', None),
        ('interrupted', TURN_FAILURE_LIMIT, 'sequence', None),
        ('stopped', 0, 'none', None),
        ('answer_malformed', 2, 'sequence', None),
        ('context_unverified', 2, 'sequence', None),
        ('scientific_refused', SINGLE_RETRY_LIMIT, 'role', None),
        ('format_unsupported', 1, 'decision', None),
        ('unknown', 2, 'sequence', None))
}


class ReplacementRecovery(TypedDict):
    kind: Literal['replace']
    reason: str
    payload: Dict[str, Any]
    preflight: Dict[str, Any]
    details: Dict[str, Any]


class DecisionRecovery(TypedDict):
    kind: Literal['decision']
    code: str
    owner_decides: str
    then: List[Dict[str, Any]]
    details: Dict[str, Any]


RecoveryResult = Union[ReplacementRecovery, DecisionRecovery]


def calculate_over_cap_allowance(share=None, turns=66):
    """Bound attempts at distinct levels; identical runs always have their own cap."""
    if share is None:
        return 2
    for count in range(2, 8):
        if (1 - share ** count) ** turns >= 0.99:
            return count
    return 7


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
# SDKCompactBoundaryMessage, read 2026-11-14:
# https://code.claude.com/docs/en/agent-sdk/typescript#sdkcompactboundarymessage
CLAUDE_COMPACTION_SUBTYPE = 'compact_boundary'


def find_capacity_signal(route, attempt, response, answer):
    """Recognize size evidence without learning any limit or token ratio from it."""
    error = attempt.get('error') or {}
    code = error.get('code')
    if code == 'review_response_too_large':
        status = (error.get('details') or {}).get('status')
        outcome = ('usage_limit' if status == 429 else 'provider_unavailable') if (
            type(status) is int and not 200 <= status < 300) else 'response_over_limit'
        return dict(outcome=outcome, signal=dict(source='attempt.error.code', value=code, status=status), reset_at=None)
    maximum = _OUTPUT_TOKEN_MAXIMUM_ERROR.match(answer.lstrip())
    if maximum is not None:
        from .review_route_capacity import describe_route_capacity
        stated = int(maximum.group(1))
        signal = dict(source='response.answer', value=answer, max_output_tokens=stated)
        if 'for a request sent without streaming' in answer:
            signal['detail'] = 'non_streaming_fallback'
            return dict(outcome='execution_incomplete', signal=signal, reset_at=None)
        if stated < describe_route_capacity(route)['output_tokens']['value']:
            signal['detail'] = 'reduced_by_context'
        return dict(outcome='output_over_cap', signal=signal, reset_at=None)
    if answer.lstrip().startswith('Prompt is too long'):
        return dict(outcome='input_over_limit', signal=dict(source='response.answer', value=answer), reset_at=None)
    diagnostic = attempt.get('provider_diagnostic') or {}
    if diagnostic.get('codex_error_info') == 'context_window_exceeded':
        return dict(outcome='input_over_limit', signal=dict(
            source='provider_diagnostic.codex_error_info', value='context_window_exceeded'), reset_at=None)
    issues = (response.get('context_evidence') or {}).get('issues', [])
    if route['adapter'] in _CODEX_ADAPTERS and issues == ['uninspected_compaction']:
        return dict(outcome='input_over_limit', signal=dict(
            source='response.context_evidence.issues', value=issues, detail='compacted'), reset_at=None)
    if route['adapter'] == 'claude_cli_v1' and issues == ['system_event_' + CLAUDE_COMPACTION_SUBTYPE]:
        for entry in (response.get('execution') or {}).get('system_events', {}).get('events', []):
            try:
                event = json.loads(base64.b64decode(entry['raw_base64'], validate=True))
            except (KeyError, ValueError, TypeError):
                continue
            if isinstance(event, dict) and event.get('type') == 'system' and event.get('subtype') == CLAUDE_COMPACTION_SUBTYPE:
                return dict(outcome='input_over_limit', signal=dict(
                    source='response.execution.system_events', value=event, detail='compacted'), reset_at=None)
    return None


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
    from .review_route_capacity import describe_route_capacity
    maximum = describe_route_capacity(route)['output_tokens']['value']
    # Restrict the existing predicates to this exact attempt; another call cannot decide its outcome.
    evidence = dict(records, review_assignment={assignment['id']: assignment},
                    review_attempt={attempt['id']: attempt})
    packet = protocol._read_json(artifacts, assignment['packet']) if assignment.get('packet') else {}
    header = packet.get(parts.KEY, {})
    if parts.find_kind(packet) == 'read' and maximum is not None:
        detected = parts.find_failed_replacement(evidence, artifacts, assignment, header['adapter'],
            header['series_key'], assignment.get('slot_id'),
            assignment.get('context', {}).get('prior_assignment_id'), maximum, capacity_events=False)
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


def find_safeguard_notice(response):
    """Find the CLI's retained safeguard notice without granting input verification."""
    for entry in response.get('execution', {}).get('system_events', {}).get('events', []):
        try:
            event = json.loads(base64.b64decode(entry['raw_base64'], validate=True))
        except (KeyError, ValueError, TypeError):
            continue
        if (isinstance(event, dict) and event.get('type') == 'system'
                and event.get('subtype') == 'informational' and isinstance(event.get('content'), str)
                and 'safeguards' in event['content'].lower()):
            return event
    return None


def classify_attempt(records, artifacts, attempt):
    """Return the first decided failure layer, or None for a usable completed output."""
    from .review_refusal import refused_attempts
    if not (attempt['status'] == 'completed' and attempt.get('output') is not None):
        for stop in records.get('review_stop', {}).values():
            if stop['mode'] == 'now' and any(
                    (call['operation'] == 'review.invoke' and call.get('invocation_id') == attempt['id'])
                    or (attempt.get('reservation_id') is not None
                        and call.get('reservation_id') == attempt['reservation_id']) for call in stop['calls']):
                return dict(outcome='stopped', signal=dict(source='review_stop', value=stop['id']), reset_at=None)
    if (attempt['status'] == 'completed' and attempt.get('output') is not None
            and not refused_attempts(records, artifacts, [attempt])
            and attempt.get('reservation_id') not in records.get('review_task_refusal', {})
            and protocol._context_observed(artifacts, attempt)):
        return None
    assignment = records['review_assignment'][attempt['assignment_id']]
    response, answer = read_attempt_evidence(artifacts, attempt)
    safeguard_notice = find_safeguard_notice(response)
    error_code = (attempt.get('error') or {}).get('code')
    diagnostic = attempt.get('provider_diagnostic') or {}
    usage_line = re.match(r"^You['’]ve (?:hit|reached) your\b[^\n]*\blimit\b", answer.strip())
    capacity_signal = find_capacity_signal(records['review_route'][assignment['route_id']], attempt, response, answer)
    outcome, signal, reset_at = 'unknown', dict(source='retained_evidence', value='undecided'), None
    dispatch = {'review_runtime_missing': 'runtime_unavailable',
                'review_invocation_interrupted': 'interrupted',
                'review_route_runtime_stale': 'runtime_stale',
                'review_format_unsupported': 'format_unsupported', 'review_input_too_large': 'input_over_limit'}
    if error_code in dispatch:
        outcome, signal = dispatch[error_code], dict(source='attempt.error.code', value=error_code)
    elif capacity_signal is not None:
        return capacity_signal
    elif safeguard_notice is not None:
        outcome = 'provider_refused'
        signal = dict(source='response.execution.system_events', value=safeguard_notice)
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
        elif ('incomplete_turn' in issues or 'tool_or_injected_input' in issues
              or (bool(answer.strip()) and response.get('status') == 'incomplete')):
            outcome = 'execution_incomplete'
            if 'incomplete_turn' in issues or 'tool_or_injected_input' in issues:
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
    from .review_refusal_replacement import read_position_context
    capacity_chain = assignment.get('capacity_plan') and assignment.get('reading_chain')
    if capacity_chain:
        while assignment.get('capacity_replacement_of'):
            assignment = records['review_assignment'][assignment['capacity_replacement_of']]
    parent = assignment.get('context', {}).get('prior_assignment_id')
    result = []
    for other in records.get('review_assignment', {}).values():
        if other.get('role') != assignment['role']:
            continue
        context = other.get('context', {})
        if parent is not None:
            # find_replacement groups same-parent continuations, including the
            # supported prospective change from the Codex text to visual route.
            if (context.get('prior_assignment_id') == parent
                    and context.get('unread') == assignment.get('context', {}).get('unread')):
                result.append(other['id'])
            continue
        if any(other.get(key) != assignment.get(key) for key in ('route_id', 'dossier_id', 'subject')):
            continue
        if any(normalized_text(other[key]) != normalized_text(assignment[key])
               for key in ('reviewer_id', 'author_id')):
            continue
        if read_position_context(other) == read_position_context(assignment):
            result.append(other['id'])
    if capacity_chain:
        for other in sorted(records.get('review_assignment', {}).values(),
                            key=lambda row: row['recorded_revision']):
            if other.get('capacity_replacement_of') in result and other['id'] not in result:
                result.append(other['id'])
    return result


def plan_output_replacement(records, artifacts, assignment, *, allow_single=True, include_packet=False,
                            outcome='output_over_cap', replacement_id=None):
    """Prove the existing handler produces a smaller request that passes preflight."""
    from .review_staged_protocol import ControllerArtifacts
    from . import review_preflight
    from .review_problem_parts import _measure

    packet = protocol._read_json(artifacts, assignment['packet'])
    from .review_route_capacity import describe_task_kind
    from .review_single_request import build_output_replacement, is_first_output_read
    single = describe_task_kind(assignment['role'], packet) == 'single'
    if (single or is_first_output_read(assignment, packet)) and not repair.applies(assignment, packet):
        if single and not allow_single:
            return None
        staged = artifacts if include_packet else ControllerArtifacts(artifacts)
        try:
            replacement = build_output_replacement(records, staged, assignment, identifier=replacement_id)
        except ResearchError:
            return None
        if replacement is None:
            return None
        result = dict(state='replace', code=OUTCOME_CODES['output_over_cap'], handler='single_request_series',
                      unit_characters=replacement['packet'][parts.KEY]['unit_characters'],
                      context=replacement['context'], preflight=replacement['preflight'])
        if include_packet:
            result.update(packet=replacement['packet'], prompt=replacement['prompt'])
        return result
    is_read = parts.find_kind(packet) == 'read'
    is_repair = repair.applies(assignment, packet)
    if not is_read and not is_repair:
        return None
    route = records['review_route'][assignment['route_id']]
    staged = artifacts if include_packet else ControllerArtifacts(artifacts)
    logical = parts.read_logical_packet(staged, packet[parts.KEY]) if is_read else packet
    value = {key: copy.deepcopy(assignment[key]) for key in
             ('id', 'role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id', 'context')}
    value['id'] = replacement_id or value['id'] + '-replacement'
    if 'subject' in assignment:
        value['subject'] = copy.deepcopy(assignment['subject'])
    base = assignment['prompt']['parameters']
    if is_read:
        parameters = dict(base['base_prompt']['parameters'])
    else:
        parameters = dict(repair=bool(base.get('context_repair')),
            orchestration=base.get('orchestration'), excerpts='reviewer_excerpts' in base,
            common_version=protocol.find_common_prompt_version(assignment['prompt']))
    if base.get('context_repair_exhaustion') == protocol.context_repair.EXHAUSTION_PROTOCOL:
        parameters['repair_prompt'] = protocol.context_repair.EXHAUSTION_PROMPT_V1
    from .review_prompt_paragraphs import read_paragraph_parameters
    parameters.update(read_paragraph_parameters(assignment['prompt']))
    prompt = protocol.canonical_prompt(assignment['role'], **parameters)
    slot_id = protocol.context_repair.slot_id(records, assignment)
    history = protocol.read_history(staged, assignment)
    prepare = repair.prepare_packet if is_repair else parts.prepare_packet
    try:
        options = dict(base_prompt_parameters=parameters, slot_id=slot_id,
                       refusal_notice=packet.get('output_refusal'))
        if outcome == 'input_over_limit':
            options['capacity_replacement'] = dict(input_over_limit=True,
                unit_characters=packet[parts.KEY]['unit_characters'] if is_read else parts.READ_CHARACTERS)
        if is_repair and not is_read and 'context_repair' in value['context']:
            predecessor = records['review_assignment'][value['context']['context_repair']['assignment_id']]
            chain = protocol.context_repair._exposure_lineage(records, predecessor)
            scientific_history = protocol.context_repair.collect_scientific_history(
                records, staged, chain, protocol.context_repair.ROLES + ('adjudicator',),
                exhausted_position=logical['context_repair'].get('exhaustion', {}).get('position', ()))
            predecessor_state = protocol.assignment_state(records, staged, predecessor['id'])
            options['repair'] = dict(scientific_history=scientific_history,
                predecessor_binding=protocol.context_repair.bind_predecessor(
                    records, predecessor, predecessor_state, scientific_history))
        candidate = prepare(records, staged, value, route, logical, prompt, history, **options)
        if parts.find_kind(candidate) != 'read':
            return None
        candidate = protocol.prepare_capacity_notice(records, route, assignment['role'], candidate)
        header = candidate[parts.KEY]
        if is_read and header['unit_characters'] >= packet[parts.KEY]['unit_characters']:
            return None
        turn_prompt = parts.build_turn_prompt(assignment['role'], parameters, header)
        from . import review_prompt_paragraphs as paragraphs
        selected = paragraphs.select_prompt_parameters(assignment['role'], candidate)
        if paragraphs.CAPACITY_PARAMETER in selected or paragraphs.CAPACITY_PARAMETER in turn_prompt['parameters']:
            base_prompt = paragraphs.strip_prompt_paragraphs(turn_prompt, assignment['role'])
            recorded = paragraphs.read_paragraph_parameters(turn_prompt)
            turn_prompt = paragraphs.append_prompt_paragraphs(base_prompt, assignment['role'],
                recorded.get(paragraphs.CARRY_PARAMETER), selected.get(paragraphs.PARAMETER),
                selected.get(paragraphs.CAPACITY_PARAMETER))
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
    replacement = dict(state='replace', code=OUTCOME_CODES['output_over_cap'],
                handler='context_repair' if is_repair else 'parts_v2_read',
                unit_characters=header['unit_characters'],
                context=dict(capacity_replacement=assignment['id']), preflight=preflight)
    if include_packet:
        replacement.update(packet=candidate, prompt=turn_prompt)
    return replacement


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


def should_reduce_capacity_task(records, artifacts, assignment):
    """Let the parts builder consume D2's events after their identical-run allowance.

    The classifier's legacy detector disables this extension, so detecting an output
    event never turns a timeout or a safeguard stop into an output event.
    """
    position = count_position(records, artifacts, assignment)
    if not position['failures']:
        return False
    outcome = position['failures'][-1][1]['outcome']
    used = position['failures_by_outcome'][outcome]
    return (outcome in ('output_over_cap', 'input_over_limit')
            or outcome == 'timeout' and used >= 2
            or outcome == 'provider_refused' and used >= PROVIDER_REFUSAL_ATTEMPT_LIMIT)


def _is_scheduled_unknown(records, artifacts, failures):
    """Retain the Codex rule for a prefix of cheap empty failures."""
    for attempt, _ in failures:
        _, answer = read_attempt_evidence(artifacts, attempt)
        seconds = (attempt.get('usage') or {}).get('wall_seconds')
        if answer.strip() or type(seconds) not in (int, float) or not 0 <= seconds <= CHEAP_FAILURE_SECONDS:
            return False
    assignment = records['review_assignment'][failures[-1][0]['assignment_id']]
    return records['review_route'][assignment['route_id']]['adapter'] in _CODEX_ADAPTERS


def _describe_sequence_counts(records, artifacts, failures):
    counts = {}
    provider_count = 0
    for index, (attempt, item) in enumerate(failures):
        outcome = item['outcome']
        counts[outcome] = counts.get(outcome, 0) + 1
        eligible = outcome in _PROVIDER_OUTCOMES or (
            outcome == 'unknown' and _is_scheduled_unknown(records, artifacts, failures[:index + 1]))
        provider_count += eligible
    return counts, provider_count, eligible


def has_smaller_task(artifacts, previous, assignment):
    """Read the level change that the existing parts handler stored in its packets.

    D1 extends this reader when it stores levels for the other turn kinds.
    A new assignment id by itself does not start a smaller task or a sequence.
    """
    old = protocol._read_json(artifacts, previous['packet'])
    new = protocol._read_json(artifacts, assignment['packet'])
    if assignment.get('capacity_plan') and assignment.get('reading_chain'):
        before = old.get('reading_turn', old.get('reading_final', {})).get('capacity', {})
        after = new.get('reading_turn', new.get('reading_final', {})).get('capacity', {})
        return (after.get('material_allowance', float('inf')) < before.get('material_allowance', 0)
                or after.get('answer_bound', float('inf')) < before.get('answer_bound', 0))
    if parts.find_kind(new) != 'read':
        return False
    return (parts.find_kind(old) != 'read'
            or new[parts.KEY]['unit_characters'] < old[parts.KEY]['unit_characters'])


def count_position(records, artifacts, assignment):
    """Count sequences from retained attempts, replacements, stops and owner events."""
    from .review_refusal_replacement import read_replacement_link
    identifiers = set(list_turn_assignments(records, assignment))
    attempts = [row for row in records.get('review_attempt', {}).values()
                if row['assignment_id'] in identifiers]
    events = [row for row in records.get('review_context_event', {}).values()
              if row['assignment_id'] in identifiers and row['kind'] == 'observation'
              and row['source'] == 'owner']
    members = [records['review_assignment'][key] for key in identifiers]
    ordered = sorted([(row.get('recorded_revision', 0), 'attempt', row) for row in attempts]
                     + [(row.get('recorded_revision', 0), 'event', row) for row in events]
                     + [(row.get('recorded_revision', 0), 'assignment', row) for row in members],
                     key=lambda item: (item[0], item[1], item[2]['id']))
    failures, sequence, permitted, ended = [], 1, SEQUENCE_LIMIT, False
    over_cap, replacements, complete = 0, dict(capacity=0, output_refused=0), False
    over_cap_by_assignment, over_cap_by_level = {}, {}
    levels, previous_member, level = {}, None, 0
    for member in members:
        if member['id'] == min(members, key=lambda row: row.get('recorded_revision', 0))['id']:
            continue
        reason, _ = read_replacement_link(member)
        replacements[reason] += 1
    counts, provider_count, eligible = {}, 0, False
    sequence_ends, attempt_rows = {}, []
    for revision, kind, row in ordered:
        if kind == 'assignment':
            if previous_member is not None and has_smaller_task(artifacts, previous_member, row):
                level += 1
                if ended and len(failures) < TURN_FAILURE_LIMIT and failures[-1][1]['outcome'] in (
                        'provider_refused', 'output_over_cap', 'input_over_limit', 'response_over_limit', 'timeout'):
                    ended = False
                    sequence_ends.pop(sequence, None)
            levels[row['id']] = level
            previous_member = row
            continue
        if kind == 'event':
            if permitted in sequence_ends and revision > sequence_ends[permitted]:
                permitted += 1
            continue
        classification = classify_attempt(records, artifacts, row)
        attempt_rows.append(dict(attempt_id=row['id'], assignment_id=row['assignment_id'],
            sequence=sequence + int(ended), outcome=classification['outcome'] if classification else 'completed',
            time=row.get('observed_at')))
        if classification is None:
            if row['assignment_id'] != assignment['id']:
                continue
            failures, ended, complete = [], False, True
            counts, provider_count, eligible = {}, 0, False
            break
        if classification['outcome'] == 'stopped':
            continue
        complete = False
        if ended:
            sequence += 1
            failures = []
        failures.append((row, classification))
        outcome = classification['outcome']
        over_cap += outcome == 'output_over_cap'
        if outcome == 'output_over_cap':
            key = row['assignment_id']
            over_cap_by_assignment[key] = over_cap_by_assignment.get(key, 0) + 1
            task_level = levels.get(key, 0)
            over_cap_by_level[task_level] = over_cap_by_level.get(task_level, 0) + 1
        counts, provider_count, eligible = _describe_sequence_counts(records, artifacts, failures)
        limit = len(PROVIDER_WAIT_SECONDS) + 1 if eligible else OUTCOME_ROWS[outcome]['attempt_limit']
        used = provider_count if eligible else counts[outcome]
        ended = len(failures) >= TURN_FAILURE_LIMIT or used >= limit
        if ended:
            sequence_ends[sequence] = revision
    return dict(sequence=sequence, attempts=attempt_rows, failures=failures, failures_by_outcome=counts,
                failed_attempts=len(failures), sequence_ended=ended,
                sequences_remaining=max(0, permitted - sequence), permitted_sequences=permitted,
                provider_count=provider_count, provider_eligible=eligible, complete=complete,
                over_cap_attempts=over_cap, over_cap_by_assignment=over_cap_by_assignment,
                over_cap_by_level=over_cap_by_level, level=levels.get(assignment['id'], 0),
                replacements=replacements)


def _plan_sequence_end(last, classification, position):
    if position['sequences_remaining'] == 0:
        return dict(state='stop', code='review_turn_failures_exhausted', not_before=None)
    observed = parse_observation_time(last)
    deadline = observed + timedelta(seconds=SEQUENCE_WAIT_SECONDS) if observed is not None else None
    reset = classification.get('reset_at')
    if reset is not None:
        reset = datetime.fromisoformat(reset)
        deadline = max(deadline, reset) if deadline is not None else reset
    return dict(state='wait' if deadline is not None else 'run',
                code='review_provider_unavailable' if position['provider_eligible'] else 'review_sequence_wait',
                not_before=deadline.isoformat(timespec='seconds') if deadline is not None else None)


REFUSAL_NOTICE_HANDOFF_MSG = (
    'The notice entry of one refusal of this position still exceeds 6,000 characters after its details are '
    'shortened, so no replacement can carry the refusal. Take the step in the details; it continues the slot as '
    'the step of an exhausted position does.')


def plan_refusal_recovery(records, artifacts, assignment):
    """Measure the current refusal through admission without publishing its candidate."""
    from . import review_output_correction as correction, review_refusal, review_refusal_replacement
    from .review_staged_protocol import ControllerArtifacts
    from .review_refusal_replacement import read_replacement_link

    completed = sorted((attempt for attempt in records.get('review_attempt', {}).values()
                        if attempt['assignment_id'] == assignment['id'] and attempt['status'] == 'completed'
                        and protocol._context_observed(artifacts, attempt)),
                       key=lambda row: row['recorded_revision'])
    members = [records['review_assignment'][identifier] for identifier in list_turn_assignments(records, assignment)]
    identifiers = {member['id'] for member in members}
    position_attempts = sorted((attempt for attempt in records.get('review_attempt', {}).values()
                                if attempt['assignment_id'] in identifiers and attempt['status'] == 'completed'),
                               key=lambda row: row['recorded_revision'])
    details = dict(assignment_id=assignment['id'],
                   refused_attempts=review_refusal.refused_attempts(records, artifacts, position_attempts))
    result = dict(state='stop', code='review_slot_exhausted', details=details)
    if not completed:
        return dict(result, state='run')
    latest = review_refusal.classify_observed_output(records, artifacts, completed[-1]['id'])
    correction_used = any(row['assignment_id'] == assignment['id']
                          for row in records.get('review_output_correction_invocation', {}).values())
    reassessment = (assignment['role'] == 'result' and assignment.get('subject', {}).get('kind') == 'problem'
                    and assignment.get('reassessment_required') is True
                    and latest is not None and latest['code'] == 'review_reassessment_mismatch'
                    and latest['check'] == 'review.reassessment'
                    and not assignment.get('context', {}).get('output_refusal'))
    if reassessment and not correction_used:
        if len(completed) == 1:
            return dict(result, state='run')
        try:
            measured = correction.build_request(records, artifacts, assignment['id'], assignment['route_id'])
        except ResearchError as error:
            if error.code == 'review_input_too_large':
                details['correction_preflight'] = error.details
        else:
            details['preflight'] = measured['preflight']
            return dict(result, state='handoff', command='review-output-correction')
    if sum(read_replacement_link(member)[0] == 'output_refused' for member in members) >= 2:
        return result
    payload = {key: copy.deepcopy(assignment[key]) for key in
               ('role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id', 'context')}
    payload['id'] = assignment['id'] + '-refusal'
    while payload['id'] in records.get('review_assignment', {}):
        payload['id'] += '-next'
    if assignment.get('capacity_plan') and assignment.get('reading_chain'):
        from .review_continuation import _find_unused_id
        payload['id'] = _find_unused_id(records, assignment['id'] + '-refusal',
                                       maximum=protocol.openness_reading.RESERVED_IDENTIFIER_CHARACTERS)
    payload['context']['output_refusal'] = {'assignment_id': assignment['id']}
    value = dict(payload)
    if 'subject' in assignment:
        value['subject'] = assignment['subject']
    staged = ControllerArtifacts(artifacts)
    revision = max(row['recorded_revision'] for row in records['review_assignment'].values()) + 1
    try:
        _, candidate = review_refusal_replacement.prepare_replacement(records, staged, value, revision)
    except ResearchError as error:
        details.update(error.details or {})
        if error.code == 'review_refusal_notice_metadata_too_large':
            details['message'] = REFUSAL_NOTICE_HANDOFF_MSG
            result['code'] = error.code
        elif error.code == 'review_refusal_replacement_ineligible' and assignment.get('reading_chain') and len(completed) == 1:
            return dict(result, state='run')
        elif error.code not in ('review_slot_exhausted', 'review_refusal_replacement_ineligible', 'review_input_too_large'):
            raise
        return result
    packet = protocol._read_json(staged, candidate['packet'])
    details['form'] = packet['output_refusal']['form']
    return dict(result, state='replace', payload=payload)


def plan_after_failure(records, artifacts, assignment_id):
    """Select the outcome row with one counter and one bounded recovery interface."""
    assignment = records['review_assignment'][assignment_id]
    position = count_position(records, artifacts, assignment)
    failures = position['failures']
    if not failures:
        return dict(state='run', not_before=None, code=None, details=dict(failed_attempts=0))
    last, classification = failures[-1]
    outcome = classification['outcome']
    row = OUTCOME_ROWS[outcome]
    details = dict(classification, failed_attempts=position['failed_attempts'], attempt_id=last['id'],
                   sequence=position['sequence'], failures_by_outcome=position['failures_by_outcome'],
                   sequences_remaining=position['sequences_remaining'], replacements=position['replacements'])
    result = dict(state='run', not_before=None, code=OUTCOME_CODES[outcome], details=details)
    used = position['failures_by_outcome'][outcome]
    if (assignment.get('context', {}).get('output_refusal')
            and not any(row['assignment_id'] == assignment_id for row in records.get('review_attempt', {}).values())):
        return result
    if outcome == 'scientific_refused' and last['assignment_id'] == assignment_id:
        recovery = plan_refusal_recovery(records, artifacts, assignment)
        recovery['details'] = dict(details, **recovery['details'])
        return dict(result, **recovery)
    if (outcome == 'provider_refused' and position['sequence_ended']
            and position['sequences_remaining'] == 0):
        from .openness_reading_v7 import plan_unread_final
        unread = plan_unread_final(records, artifacts, assignment, position)
        if unread is not None:
            return dict(result, **unread)
    if position['sequence'] > position['permitted_sequences']:
        result.update(state='stop', code='review_turn_failures_exhausted')
        return result
    if position['failed_attempts'] >= TURN_FAILURE_LIMIT and row['exhausted'] in ('sequence', 'role'):
        result.update(_plan_sequence_end(last, classification, position))
        if result['state'] == 'wait' and datetime.now(timezone.utc) >= datetime.fromisoformat(result['not_before']):
            result['state'] = 'run'
        return result
    if (assignment.get('recorded_revision', 0) > last.get('recorded_revision', 0)
            and not position['sequence_ended']):
        return result
    if (outcome in ('output_over_cap', 'input_over_limit', 'response_over_limit', 'timeout', 'provider_refused')
            and (outcome != 'response_over_limit' or assignment.get('capacity_plan'))):
        recovery = plan_capacity_recovery(records, artifacts, assignment, outcome)
        details.update(recovery['details'])
        if outcome == 'output_over_cap':
            from .review_route_capacity import describe_route_capacity
            capacity = describe_route_capacity(records['review_route'][assignment['route_id']])
            details.update(output_tokens=(last.get('usage') or {}).get('output_tokens'),
                           max_output_tokens=capacity['output_tokens']['value'],
                           over_cap_attempts=position['over_cap_attempts'])
        if (outcome != 'provider_refused' or used >= PROVIDER_REFUSAL_ATTEMPT_LIMIT) and (
                outcome != 'timeout' or used >= 2):
            allowance = calculate_over_cap_allowance(recovery['details']['at_or_over_limit_share'])
            if recovery['kind'] == 'replace' and (position['failed_attempts'] >= TURN_FAILURE_LIMIT
                    or outcome == 'output_over_cap' and len(position['over_cap_by_level']) >= allowance):
                from .review_route_capacity import describe_capacity_decision
                recovery = describe_capacity_decision(records, artifacts, assignment, outcome,
                    dict(recovery['details'], over_cap_allowance=allowance,
                         untried_material_allowance=recovery['details'].get('unit_characters')))
                result.update(state='stop')
                details.update(recovery['details'], recovery=recovery,
                               decision={key: recovery[key] for key in ('owner_decides', 'then')})
                return result
            if recovery['kind'] == 'replace':
                result.update(state='replace')
                details.update(recovery['details'], reason=recovery['reason'], recovery=recovery)
                return result
        if (outcome == 'input_over_limit' and position['failed_attempts'] < TURN_FAILURE_LIMIT
                and protocol._initial_size_failure(
                records, artifacts, assignment, assignment)):
            result['state'] = 'replace'
            details['context'] = copy.deepcopy(assignment['context'])
            return result
        if outcome in ('output_over_cap', 'input_over_limit', 'response_over_limit', 'timeout'):
            # A parts floor retains its existing terminal code. Otherwise at most
            # one identical retry is allowed, independent of a profile's c.
            floor = recovery['kind'] == 'decision' and recovery['code'] == 'review_parts_budget_exhausted'
            same_level = (position['over_cap_by_level'].get(position['level'], 0)
                          if outcome == 'output_over_cap' else used)
            if floor or outcome == 'input_over_limit' or same_level >= 2 or position['failed_attempts'] >= TURN_FAILURE_LIMIT:
                result.update(state='stop', code=recovery.get('code', OUTCOME_CODES[outcome]))
                if recovery['kind'] == 'decision':
                    details['decision'] = {key: recovery[key] for key in ('owner_decides', 'then')}
                details.update(recovery['details'], recovery=recovery)
                return result
    if position['sequence_ended']:
        if row['exhausted'] == 'sequence':
            result.update(_plan_sequence_end(last, classification, position))
        else:
            result['state'] = 'handoff' if row['exhausted'] == 'handoff' else 'stop'
            if outcome == 'runtime_unavailable':
                if position['sequences_remaining'] == 0:
                    result.update(state='stop', code='review_turn_failures_exhausted')
                else:
                    result['not_before'] = _plan_sequence_end(last, classification, position)['not_before']
    elif row['schedule'] or position['provider_eligible']:
        count = position['provider_count'] if position['provider_eligible'] else used
        state, deadline = plan_scheduled_retry(last, count, classification['reset_at'])
        result.update(state=state, not_before=deadline)
    if result['state'] == 'wait' and datetime.now(timezone.utc) >= datetime.fromisoformat(result['not_before']):
        result['state'] = 'run'
    if result['state'] == 'stop' and outcome == 'provider_refused':
        result['stop_class'] = 'decision'
    return result
