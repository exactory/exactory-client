"""Subscription-backed Claude Code review with inspection of the actual delivered input.

Every invocation runs the Claude Code CLI as its own print-mode process in a fresh, empty working directory: safe mode
(no CLAUDE.md, skills, plugins, hooks, MCP servers or auto memory), no built-in tools, a replaced system prompt, no
session persistence and no permission prompts. The packet goes in as one stream-json user message that the CLI echoes
back. The adapter checks the session initialization, the echoed input and every later event of the stream; it never
uses the reviewer's self-report. A positive-control probe is still required by review_protocol.
"""

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from .errors import ResearchError
from .evidence import digest
# The owned-process runner of the Codex adapter is reused unchanged. That module's digest is part of every recorded
# Codex attempt, so the runner stays where it is instead of moving into a shared module.
from .review_codex_transport import _run_owned

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


def clean_environment(environment):
    """Keep the subscription login at its real home, without API credentials or the parent session's variables."""
    return {key: value for key, value in environment.items() if key in ENVIRONMENT}


def runtime():
    """The isolation contract of the route. The installed CLI version is recorded with each invocation instead: the CLI
    updates itself, and an update must not invalidate the reviews that an earlier version recorded."""
    return {'adapter': ADAPTER, 'flags': list(FLAGS), 'input_contract': INPUT_CONTRACT,
            'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'credential_mode': 'claude-subscription', 'output_limit': 'checked-after-generation',
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


def inspect_stream(events, request, route, control):
    """Inspect the observed stream of one invocation; never the reviewer's self-report."""
    expected = packet_text(request)
    issues, outputs, replays, sessions = [], [], [], set()
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
    for event in events:
        kind = event.get('type')
        if event.get('session_id'):
            sessions.add(event['session_id'])
        if control in json.dumps(event, ensure_ascii=False):
            issues.append('excluded_control_exposed')
        if kind == 'system':
            if event.get('subtype') != 'init':
                issues.append('system_event_' + str(event.get('subtype')))
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
    return {'input_verified': not issues, 'issues': sorted(set(issues)),
            'session_id': next(iter(sessions)) if len(sessions) == 1 else None,
            'request_digest': digest(request), 'packet_digest': digest(expected), 'event_digest': digest(events),
            'assistant_output_digest': digest(outputs), 'excluded_controls': [control],
            'observed_events': len(events), 'input_contract': INPUT_CONTRACT}


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
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError('Event is not an object')
            events.append(event)
        except ValueError:
            malformed.append(hashlib.sha256(line.encode()).hexdigest())
    return events, malformed


def send_request(route, request, credential=None):
    validate_route(route)
    text = packet_text(request)
    if len(text) > INPUT_LIMIT_CHARACTERS:
        raise ResearchError('review_input_too_large', 'The reviewer input exceeds the Claude route limit of '
                            + str(INPUT_LIMIT_CHARACTERS) + ' characters')
    executable, version = _cli()
    environment = clean_environment(os.environ)
    config = route['configuration']
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
    events, malformed = _parse_stream(result.stdout)
    evidence = inspect_stream(events, request, route, control)
    evidence.update(runtime=runtime(), route_digest=digest(route), cli_version=version, inspected=True)
    results = [event for event in events if event.get('type') == 'result']
    final = results[-1] if results else {}
    usage = final.get('usage') if isinstance(final.get('usage'), dict) else {}
    counts = [usage.get(key) for key in ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')]
    input_tokens = sum(counts) if all(type(count) is int for count in counts) else None
    output_tokens = usage.get('output_tokens') if type(usage.get('output_tokens')) is int else None
    complete = (result.returncode == 0 and not malformed and len(results) == 1 and final.get('subtype') == 'success'
                and final.get('is_error') is False and output_tokens is not None
                and output_tokens <= config['max_output_tokens'])
    return {'id': evidence['session_id'] or 'failed-' + uuid.uuid4().hex, 'model': route['model'],
            'status': 'completed' if complete else 'incomplete',
            'output': [{'type': 'message', 'role': 'assistant',
                        'content': [{'type': 'output_text', 'text': final.get('result') if isinstance(final.get('result'), str) else ''}]}],
            'usage': {'input_tokens': input_tokens, 'output_tokens': output_tokens,
                      'cost_usd_estimate': final.get('total_cost_usd')},
            'context_evidence': evidence,
            'execution': {'returncode': result.returncode, 'timeout': result.returncode is None,
                          'stdout_digest': hashlib.sha256(result.stdout.encode()).hexdigest(),
                          'stderr_digest': hashlib.sha256((result.stderr or '').encode()).hexdigest(),
                          'malformed_line_digests': malformed, 'event_count': len(events),
                          'event_types': [event.get('type') for event in events]}}


def verify_context(route, request, response):
    evidence = response.get('context_evidence', {})
    return (evidence.get('inspected') is True and evidence.get('input_verified') is True
            and evidence.get('request_digest') == digest(request)
            and evidence.get('route_digest') == digest(route)
            and evidence.get('input_contract') == INPUT_CONTRACT and bool(evidence.get('event_digest'))
            and evidence.get('session_id') == response.get('id')
            and evidence.get('runtime') == runtime())
