"""Subscription-backed Claude Code review with inspection of the actual delivered input.

Every invocation runs the Claude Code CLI as its own print-mode process in a fresh, empty working directory: safe mode
(no CLAUDE.md, skills, plugins, hooks, MCP servers or auto memory), no built-in tools, a replaced system prompt, no
session persistence and no permission prompts. The packet goes in as one stream-json user message that the CLI echoes
back. The adapter checks the session initialization, the echoed input and every later event of the stream; it never
uses the reviewer's self-report. A positive-control probe is still required by review_protocol.
"""

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from .errors import ResearchError
from .evidence import digest
# Reuse the owned-process cleanup unchanged; the Claude capture also preserves the original stream bytes.
from .review_codex_transport import _stop_owned_process

ADAPTER = 'claude_cli_v1'
ENDPOINT = 'claude://local'
INPUT_CONTRACT = 'observed-print-stream-v1'
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
MAX_TIMEOUT_SECONDS = 3600
# A conservative bound below the one-million-token window of the current Claude models. The CLI reports the actual
# token count of every invocation, and that count is recorded with the attempt.
INPUT_LIMIT_CHARACTERS = 1_300_000
# Each flag removes a context source or a capability. The stream of every invocation shows the result.
FLAGS = ('-p', '--safe-mode', '--tools', '', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
         '--disable-slash-commands', '--no-session-persistence', '--permission-prompts', 'none',
         '--input-format', 'stream-json', '--replay-user-messages', '--output-format', 'stream-json', '--verbose')
ENVIRONMENT = ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'TMPDIR', 'CLAUDE_CONFIG_DIR')
SYSTEM_PROMPT_FILE = 'system-prompt.txt'
OUTPUT_CONTENT_TYPES = ('text', 'thinking', 'redacted_thinking')
# Only the basic redraw notification is context-free. The CLI also defines optional fields (notably
# prompts_screened) that can concern input handling; any extra field must remain an issue.
UI_NOTICE_SUBTYPES = ('ui_invalidate',)
UI_NOTICE_FIELDS = frozenset(('type', 'subtype', 'event', 'uuid', 'session_id'))
# The basic SDKAPIRetryMessage in Claude Code 2.1.310. Its optional no_response payload remains an issue.
API_RETRY_FIELDS = frozenset(('type', 'subtype', 'attempt', 'max_retries', 'retry_delay_ms', 'error_status',
                              'error', 'uuid', 'session_id'))
API_RETRY_ERRORS = ('authentication_failed', 'oauth_org_not_allowed', 'account_on_hold', 'verification_required',
                    'billing_error', 'rate_limit', 'overloaded', 'invalid_request', 'model_not_found',
                    'server_error', 'unknown', 'max_output_tokens', 'cloud_credential_error')
SYSTEM_EVENT_COUNT_LIMIT = 4096
SYSTEM_EVENT_BYTE_LIMIT = 4 * 1024 * 1024
# SHA-256 of this file at every release tag from exactory--v0.60.0 through exactory--v0.63.0.
LEGACY_UI_IMPLEMENTATIONS = frozenset(('7b8facd0f6f679dee371b9f0b46ca0d6b2ad799ea0dfec9efe215d95402a4800',))
RESULT_RETRACTION_LINE = 'RETRACTED'
RESULT_FENCE_LINES = ('```', '```json')
RESULT_END_PATTERN = re.compile(r'[ \t]*(?:\r\n?|\n|\Z)')


def clean_environment(environment):
    """Keep the subscription login at its real home, without API credentials or the parent session's variables."""
    return {key: value for key, value in environment.items() if key in ENVIRONMENT}


def runtime():
    """The isolation contract of the route. The installed CLI version is recorded with each invocation instead: the CLI
    updates itself, and an update must not invalidate the reviews that an earlier version recorded."""
    return {'adapter': ADAPTER, 'flags': list(FLAGS), 'input_contract': INPUT_CONTRACT,
            'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'credential_mode': 'claude-subscription',
            'output_limit': 'CLAUDE_CODE_MAX_OUTPUT_TOKENS=max_output_tokens, checked after generation',
            'wall_limit': 'owned-process-group-timeout'}


def validate_route(route):
    if route['adapter'] != ADAPTER or route['endpoint'] != ENDPOINT:
        raise ResearchError('invalid_review_route', 'Use claude_cli_v1 at claude://local')
    config = route['configuration']
    if set(config) != {'effort', 'max_output_tokens', 'timeout_seconds'}:
        raise ResearchError('invalid_review_route', 'Configure effort, max_output_tokens and timeout_seconds')
    if config['effort'] not in EFFORTS:
        raise ResearchError('invalid_review_route', 'The effort is one of ' + ', '.join(EFFORTS))
    if type(config['max_output_tokens']) is not int or config['max_output_tokens'] < 1:
        raise ResearchError('invalid_review_route', 'The observed output-token bound must be positive')
    if type(config['timeout_seconds']) not in (int, float) or not 0 < config['timeout_seconds'] <= MAX_TIMEOUT_SECONDS:
        raise ResearchError('invalid_review_route', 'The execution timeout must be positive and at most '
                            + str(MAX_TIMEOUT_SECONDS) + ' seconds')


def packet_text(request):
    for item in request['input']:
        if not isinstance(item.get('content'), str):
            raise ResearchError('review_format_unsupported', 'The Claude reviewer receives text evidence only')
    return json.dumps({'review_messages': request['input']}, sort_keys=True, ensure_ascii=False)


def _replayed_text(message):
    content = message.get('content') if isinstance(message, dict) else None
    if not isinstance(content, list) or any(not isinstance(part, dict) or part.get('type') != 'text'
                                            or not isinstance(part.get('text'), str) for part in content):
        return None
    return '\n'.join(part['text'] for part in content)


def _ui_notice(event):
    return (event.get('subtype') in UI_NOTICE_SUBTYPES and set(event) == UI_NOTICE_FIELDS
            and event['event'] == 'ui.render'
            and all(isinstance(event[key], str) and event[key] for key in ('uuid', 'session_id')))


def _is_api_retry_notice(event):
    return (event.get('subtype') == 'api_retry' and set(event) == API_RETRY_FIELDS
            and all(type(event[key]) is int for key in ('attempt', 'max_retries', 'retry_delay_ms'))
            and (event['error_status'] is None or type(event['error_status']) is int)
            and event['error'] in API_RETRY_ERRORS
            and all(isinstance(event[key], str) and event[key] for key in ('uuid', 'session_id')))


def _classify_system_event(event, single_generation):
    if _ui_notice(event) or (single_generation and _is_api_retry_notice(event)):
        return 'notice'
    # thinking_tokens reports the thinking budget that the turn used; it carries no context and no capability.
    if event.get('subtype') in ('init', 'thinking_tokens'):
        return None
    return 'system_event_' + str(event.get('subtype'))


def _read_result_usage(result):
    usage = result.get('usage') if isinstance(result.get('usage'), dict) else {}
    counts = [usage.get(key) for key in ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')]
    input_tokens = sum(counts) if all(type(count) is int and count >= 0 for count in counts) else None
    output_tokens = usage.get('output_tokens')
    if type(output_tokens) is not int or output_tokens < 0:
        output_tokens = None
    return {'input_tokens': input_tokens, 'output_tokens': output_tokens,
            'cost_usd_estimate': result.get('total_cost_usd')}


def _build_result_evidence(events, outputs, answer=None):
    results = [(index, event) for index, event in enumerate(events) if event.get('type') == 'result']
    if len(results) != 1:
        return {'single_generation': False}
    index, result = results[0]
    usage = _read_result_usage(result)
    text = result.get('result')
    if isinstance(text, str) and answer is None:
        answer = parse_result_text(text)[0]
    verified = (index == len(events) - 1 and result.get('subtype') == 'success'
                and result.get('is_error') is False and result.get('num_turns') == 1
                and isinstance(text, str) and ''.join(outputs) == text
                and usage['input_tokens'] is not None and usage['output_tokens'] is not None)
    return {'single_generation': verified, 'event_index': index, 'event_digest': digest(result),
            'output_digest': digest(answer) if isinstance(text, str) else None, 'usage': usage}


def _verify_result_evidence(response):
    evidence = response.get('context_evidence', {}).get('result_evidence', {})
    output = ''.join(part['text'] for item in response.get('output', []) if item.get('type') == 'message'
                     for part in item.get('content', []) if part.get('type') == 'output_text')
    return (evidence.get('single_generation') is True and bool(evidence.get('event_digest'))
            and evidence.get('event_index') == response.get('execution', {}).get('event_count', 0) - 1
            and evidence.get('output_digest') == digest(output) and evidence.get('usage') == response.get('usage'))


def inspect_stream(events, request, route, control, *, answer=None):
    """Inspect the observed stream of one invocation; never the reviewer's self-report."""
    expected = packet_text(request)
    issues, outputs, replays, sessions = [], [], [], set()
    notices, system_events = [], []
    initializations = [event for event in events if event.get('type') == 'system' and event.get('subtype') == 'init']
    if len(initializations) != 1:
        issues.append('session_initialization')
    else:
        initialization = initializations[0]
        for key, code in (('tools', 'tools_available'), ('mcp_servers', 'mcp_servers_connected'),
                          ('skills', 'skills_loaded'), ('slash_commands', 'commands_loaded')):
            if initialization.get(key) != []:
                issues.append(code)
        if initialization.get('model') != route['model']:
            issues.append('model_mismatch')
    for index, event in enumerate(events):
        kind = event.get('type')
        if event.get('session_id'):
            if isinstance(event['session_id'], str):
                sessions.add(event['session_id'])
            else:
                issues.append('session_identity')
        if control in json.dumps(event, ensure_ascii=False):
            issues.append('excluded_control_exposed')
        if kind == 'system':
            system_events.append((index, event))
        elif kind == 'user':
            text = _replayed_text(event.get('message'))
            if event.get('isReplay') is not True or text is None:
                issues.append('tool_or_injected_input')
            else:
                replays.append(text)
        elif kind == 'assistant':
            content = (event.get('message') or {}).get('content')
            if not isinstance(content, list) or any(not isinstance(part, dict)
                                                    or part.get('type') not in OUTPUT_CONTENT_TYPES for part in content):
                issues.append('tool_use_or_unknown_output')
                continue
            outputs.append(''.join(part.get('text', '') for part in content if part.get('type') == 'text'))
        elif kind not in ('result', 'rate_limit_event'):
            issues.append('unknown_event')
    if replays != [expected]:
        issues.append('packet_or_history_mismatch')
    results = [event for event in events if event.get('type') == 'result']
    result = results[0] if len(results) == 1 else {}
    if (len(results) != 1 or result.get('subtype') != 'success' or result.get('is_error') is not False
            or result.get('num_turns') != 1):
        issues.append('incomplete_turn')
    if ''.join(outputs) != result.get('result'):
        issues.append('provider_output_mismatch')
    if len(sessions) != 1:
        issues.append('session_identity')
    result_evidence = _build_result_evidence(events, outputs, answer)
    for index, event in system_events:
        classification = _classify_system_event(event, result_evidence['single_generation'])
        if classification == 'notice':
            notices.append({'event_index': index, 'event': dict(event)})
        elif classification is not None:
            issues.append(classification)
    return {'input_verified': not issues, 'issues': sorted(set(issues)), 'notices': notices,
            'session_id': next(iter(sessions)) if len(sessions) == 1 else None,
            'request_digest': digest(request), 'packet_digest': digest(expected), 'event_digest': digest(events),
            'assistant_output_digest': digest(outputs), 'excluded_controls': [control],
            'observed_events': len(events), 'input_contract': INPUT_CONTRACT, 'result_evidence': result_evidence}


def _run_owned(command, *, environment, timeout, packet=None, directory=None):
    with subprocess.Popen(command, stdin=subprocess.PIPE if packet is not None else subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=directory, env=environment,
                          start_new_session=True) as process:
        try:
            stdout, stderr = process.communicate(None if packet is None else packet.encode('utf-8'), timeout=timeout)
        except subprocess.TimeoutExpired:
            stdout, stderr = _stop_owned_process(process)
            returncode = None
        except BaseException:
            _stop_owned_process(process)
            raise
        else:
            returncode = process.returncode
    return subprocess.CompletedProcess(command, returncode, stdout.decode('utf-8', errors='surrogateescape'),
                                       stderr.decode('utf-8', errors='surrogateescape'))


def _cli():
    """The installed CLI and its reported version, read for each invocation."""
    executable = shutil.which('claude')
    if executable is None:
        raise ResearchError('review_runtime_missing', 'The Claude Code CLI is not installed')
    result = _run_owned([executable, '--version'], environment=clean_environment(os.environ), timeout=30)
    if result.returncode != 0:
        raise ResearchError('review_runtime_missing', 'The Claude Code CLI version could not be read')
    return executable, result.stdout.strip()


def _parse_stream(raw):
    events, malformed = [], []
    retention = {'events': [], 'count_limit': SYSTEM_EVENT_COUNT_LIMIT, 'byte_limit': SYSTEM_EVENT_BYTE_LIMIT,
                 'observed_count': 0, 'observed_bytes': 0, 'retained_bytes': 0, 'truncated': False}
    for raw_line in raw.encode('utf-8', errors='surrogateescape').splitlines(keepends=True):
        line = raw_line.rstrip(b'\r\n')
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError('Event is not an object')
            if event.get('type') == 'system':
                retention['observed_count'] += 1
                retention['observed_bytes'] += len(raw_line)
                if (retention['truncated'] or len(retention['events']) >= SYSTEM_EVENT_COUNT_LIMIT
                        or retention['retained_bytes'] + len(raw_line) > SYSTEM_EVENT_BYTE_LIMIT):
                    retention['truncated'] = True
                else:
                    retention['events'].append({'event_index': len(events),
                                                'raw_base64': base64.b64encode(raw_line).decode('ascii')})
                    retention['retained_bytes'] += len(raw_line)
            events.append(event)
        except ValueError:
            malformed.append(hashlib.sha256(line).hexdigest())
    retention['sha256'] = digest(retention['events'])
    return events, malformed, retention


def inspect_retained_system_events(response):
    """Reclassify the retained system-event prefix without running the CLI or changing the recorded inspection.

    This classifies system subtypes only; it does not reconstruct missing stream input or authenticate an attempt.
    """
    retention = response.get('execution', {}).get('system_events')
    if retention is None:
        raise ResearchError('review_system_events_unavailable', 'The response does not retain system events')
    try:
        entries = retention['events']
        if not (isinstance(entries, list) and retention['sha256'] == digest(entries)
                and 0 < retention['count_limit'] <= SYSTEM_EVENT_COUNT_LIMIT
                and 0 < retention['byte_limit'] <= SYSTEM_EVENT_BYTE_LIMIT
                and len(entries) <= retention['count_limit'] and len(entries) <= retention['observed_count']
                and retention['truncated'] is (len(entries) < retention['observed_count'])):
            raise ValueError()
        issues, notices, retained_bytes, last_index = [], [], 0, -1
        single_generation = _verify_result_evidence(response)
        for entry in entries:
            index = entry['event_index']
            raw = base64.b64decode(entry['raw_base64'], validate=True)
            retained_bytes += len(raw)
            event = json.loads(raw)
            if not (type(index) is int and last_index < index < response['execution']['event_count']
                    and isinstance(event, dict) and event.get('type') == 'system'):
                raise ValueError()
            last_index = index
            classification = _classify_system_event(event, single_generation)
            if classification == 'notice':
                notices.append({'event_index': index, 'event': event})
            elif classification is not None:
                issues.append(classification)
        if not (retained_bytes == retention['retained_bytes'] <= retention['byte_limit']
                and retained_bytes <= retention['observed_bytes']
                and (retention['truncated'] or retained_bytes == retention['observed_bytes'])):
            raise ValueError()
    except (KeyError, ValueError, TypeError, binascii.Error) as error:
        raise ResearchError('review_system_events_invalid',
                            'The retained system events do not match their digest or bounds') from error
    return {'issues': sorted(set(issues)), 'notices': notices, 'truncated': retention['truncated']}


def send_request(route, request, credential=None):
    validate_route(route)
    text = packet_text(request)
    if len(text) > INPUT_LIMIT_CHARACTERS:
        raise ResearchError('review_input_too_large', 'The reviewer input exceeds the Claude route limit of '
                            + str(INPUT_LIMIT_CHARACTERS) + ' characters')
    executable, version = _cli()
    environment = clean_environment(os.environ)
    config = route['configuration']
    # The route's bound is the CLI's bound for the one turn: without it the CLI stops a long answer at its own default
    # and continues in a second turn, which the inspection refuses as an injected input.
    environment['CLAUDE_CODE_MAX_OUTPUT_TOKENS'] = str(config['max_output_tokens'])
    # A synthetic project instruction is reachable in the working directory but must stay out of the model's context.
    control = 'EXCLUDED_PROJECT_' + uuid.uuid4().hex
    message = json.dumps({'type': 'user', 'message': {'role': 'user', 'content': [{'type': 'text', 'text': text}]}},
                         ensure_ascii=False) + '\n'
    with tempfile.TemporaryDirectory(prefix='review-context-', dir=environment.get('TMPDIR')) as directory:
        root = Path(directory)
        (root / 'CLAUDE.md').write_text('This excluded project instruction contains ' + control + '.\n')
        (root / SYSTEM_PROMPT_FILE).write_text(request['instructions'])
        command = [executable, *FLAGS, '--system-prompt-file', str(root / SYSTEM_PROMPT_FILE),
                   '--model', route['model'], '--effort', config['effort']]
        result = _run_owned(command, packet=message, directory=directory, environment=environment,
                            timeout=config['timeout_seconds'])
    events, malformed, retention = _parse_stream(result.stdout)
    results = [event for event in events if event.get('type') == 'result']
    final = results[-1] if results else {}
    usage = _read_result_usage(final)
    output_tokens = usage['output_tokens']
    complete = (result.returncode == 0 and not malformed and len(results) == 1 and final.get('subtype') == 'success'
                and final.get('is_error') is False and output_tokens is not None
                and output_tokens <= config['max_output_tokens'])
    answer, fenced, correction_evidence = parse_result_text(
        final.get('result') if isinstance(final.get('result'), str) else '')
    evidence = inspect_stream(events, request, route, control, answer=answer)
    evidence.update(runtime=runtime(), route_digest=digest(route), cli_version=version, inspected=True)
    return {'id': evidence['session_id'] or 'failed-' + uuid.uuid4().hex, 'model': route['model'],
            'status': 'completed' if complete else 'incomplete',
            'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': answer}]}],
            'usage': usage,
            'context_evidence': evidence,
            'execution': {'returncode': result.returncode, 'timeout': result.returncode is None,
                          'stdout_digest': hashlib.sha256(result.stdout.encode('utf-8', 'surrogateescape')).hexdigest(),
                          'stderr_digest': hashlib.sha256(
                              (result.stderr or '').encode('utf-8', 'surrogateescape')).hexdigest(),
                          'malformed_line_digests': malformed, 'event_count': len(events),
                          'event_types': [event.get('type') for event in events], 'fence_removed': fenced,
                          'system_events': retention, **correction_evidence}}


def strip_fence(text):
    """The answer without one Markdown code fence around the whole of it, and whether a fence was removed. A print
    run has no JSON output mode and a model often fences a JSON answer. The raw result stays in the stream digests, and
    the inspection compares the raw assistant text with the raw result before this step."""
    stripped = text.strip()
    if not (len(stripped) >= 6 and stripped.startswith('```') and stripped.endswith('```')):
        return text, False
    body = stripped[3:-3]
    newline = body.find('\n')
    if newline != -1 and body[:newline].strip().isalnum():
        body = body[newline + 1:]
    return body.strip(), True


def parse_result_text(text):
    """Read complete object blocks separated only by exact retractions and blank lines.
    A fenced block holds one column-zero object between exact triple-backtick lines. Match the whole answer
    in one pass; syntax/recursion errors, incomplete grammar and single blocks keep existing handling."""
    answer, fenced = strip_fence(text)
    decoder = json.JSONDecoder(parse_int=str, parse_float=str, parse_constant=str)
    offset, decoded_end, retraction_count, last_start, last_end = 0, 0, 0, 0, 0
    state = 'block'
    for line in text.splitlines(keepends=True):
        line_start = offset
        offset += len(line)
        if line_start < decoded_end:
            continue
        line_text = line.rstrip('\r\n')
        if not line_text.strip(' \t'):
            continue
        if state == 'closing_fence':
            if line_text != '```':
                return answer, fenced, {}
            state = 'retraction'
            continue
        if state == 'retraction':
            if line_text != RESULT_RETRACTION_LINE:
                return answer, fenced, {}
            retraction_count += 1
            state = 'block'
            continue
        if state != 'fenced_object' and line_text.lower() in RESULT_FENCE_LINES:
            state = 'fenced_object'
            continue
        if not line_text.startswith('{'):
            return answer, fenced, {}
        try:
            _, end = decoder.raw_decode(text, line_start)
        except (ValueError, RecursionError):
            return answer, fenced, {}
        if RESULT_END_PATTERN.match(text, end) is None:
            return answer, fenced, {}
        decoded_end = end
        last_start, last_end = line_start, end
        state = 'closing_fence' if state == 'fenced_object' else 'retraction'
    if state != 'retraction' or not retraction_count:
        return answer, fenced, {}
    return text[last_start:last_end], fenced, {'self_corrected': True, 'retracted_object_count': retraction_count,
                                             'result_text': text,
                                             'result_text_digest': digest(text)}


def _verify_context_bindings(route, request, response):
    evidence = response.get('context_evidence', {})
    return (evidence.get('inspected') is True and evidence.get('request_digest') == digest(request)
            and evidence.get('route_digest') == digest(route)
            and evidence.get('input_contract') == INPUT_CONTRACT and bool(evidence.get('event_digest'))
            and evidence.get('session_id') == response.get('id')
            and evidence.get('runtime') == route['runtime'])


def verify_legacy_context(route, request, response):
    """Only attempt authentication uses the released adapters' legacy context; it cannot establish a new probe."""
    evidence = response.get('context_evidence', {})
    # The Ferrers local repair bound this exact issue-only shape under released 0.60.0-0.63.0 adapters.
    # Those attempts did not retain their streams, so their event fields cannot be inspected retroactively.
    # Neither draft adapter nor this release's implementation can enter this compatibility rule.
    return (evidence.get('input_verified') is False and evidence.get('issues') == ['system_event_ui_invalidate']
            and route['runtime'].get('implementation_sha256') in LEGACY_UI_IMPLEMENTATIONS
            and _verify_context_bindings(route, request, response))


def verify_context(route, request, response):
    """The evidence must come from the adapter runtime the route pins, not from whatever is installed now."""
    evidence = response.get('context_evidence', {})
    has_retry_notices = any(notice.get('event', {}).get('subtype') == 'api_retry'
                           for notice in evidence.get('notices', []))
    return (evidence.get('input_verified') is True and _verify_context_bindings(route, request, response)
            and (not has_retry_notices or _verify_result_evidence(response)))
