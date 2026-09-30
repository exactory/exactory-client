"""Subscription-backed Codex review with inspection of the actual recorded input.

This adapter supports text evidence. Binary manuscript review uses the Responses
adapter. Every invocation starts a new local exec thread, disables configuration,
rules, retrieval and tools, and checks the recorded initial input and later tool
events. A positive-control probe is still required by review_protocol.
"""

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from .errors import ResearchError
from .evidence import digest

ADAPTER = 'codex_cli_v1'
# These complete normalized declarations were inspected on Codex 0.159.2 with
# configuration and rules disabled. A new declaration or platform stays
# unverified until its full input contract has been audited and tested.
NATIVE_DECLARATIONS = (
    'dfc8dfcb5cdb5a5382ea269f65f676cfb16dcde606c2c222beedb90c17505402',
    '47091490938505958b0c22ff42db6fd79b272a2af6923166f4c2cc53c4fe0df4',
    '6ded806e3cdbb35599ecaf8742574bc5274908472b1729090010c404c2151e8e',
)
NATIVE_ENVIRONMENTS = {'722c613b0eff734b92f076c866ff2eb55f6298c54180d9d414868d070a6c3ae8'}
CONTROLS = (
    'project_doc_max_bytes=0', 'developer_instructions=""',
    'features.memories=false', 'features.external_agent_memory_import=false',
    'features.plugins=false', 'features.apps=false', 'features.hooks=false',
    'features.shell_tool=false', 'features.multi_agent=false',
    'features.browser_use=false', 'features.computer_use=false',
    'features.skip_host_skill_discovery=true', 'features.skill_search=false',
    'web_search="disabled"', 'mcp_servers={}', 'model_reasoning_effort="low"',
)
TERMINATION_GRACE_SECONDS = 1.0


def _signal_group(process, requested_signal):
    try:
        os.killpg(process.pid, requested_signal)
        return True
    except ProcessLookupError:
        return False


def _stop_owned_process(process):
    """Stop the invocation's session, including children of an exited wrapper."""
    deadline = time.monotonic() + TERMINATION_GRACE_SECONDS
    _signal_group(process, signal.SIGTERM)
    observed = None
    try:
        observed = process.communicate(timeout=TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    # A child may close both pipes while continuing to run. Completion of
    # communicate() therefore does not establish that the owned group exited.
    while _signal_group(process, 0):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.05, remaining))
    _signal_group(process, signal.SIGKILL)
    return observed if observed is not None else process.communicate()


def _run_owned(command, *, environment, timeout, packet=None, directory=None):
    with subprocess.Popen(command, stdin=subprocess.PIPE if packet is not None else subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          encoding='utf-8', errors='replace', cwd=directory, env=environment,
                          start_new_session=True) as process:
        try:
            stdout, stderr = process.communicate(packet, timeout=timeout)
        except subprocess.TimeoutExpired:
            stdout, stderr = _stop_owned_process(process)
            return subprocess.CompletedProcess(command, None, stdout, stderr)
        except BaseException:
            _stop_owned_process(process)
            raise
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def clean_environment(environment):
    """Keep authentication at its real home, without inheriting paid API access."""
    allowed = {'PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'TMPDIR', 'CODEX_HOME'}
    return {key: value for key, value in environment.items() if key in allowed}


def runtime():
    executable = shutil.which('codex')
    if executable is None:
        raise ResearchError('review_runtime_missing', 'The Codex CLI is not installed')
    result = _run_owned([executable, '--version'], environment=clean_environment(os.environ), timeout=15)
    if result.returncode != 0:
        raise ResearchError('review_runtime_missing', 'The Codex CLI version could not be read')
    return {'adapter': ADAPTER, 'executable': str(Path(executable).resolve()),
            'version': result.stdout.strip(), 'controls': list(CONTROLS),
            'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'input_contract': 'observed-exec-rollout-v1', 'credential_mode': 'chatgpt-subscription',
            'output_limit': 'checked-after-generation', 'wall_limit': 'owned-process-group-timeout'}


def validate_route(route):
    if route['adapter'] != ADAPTER or route['endpoint'] != 'codex://local':
        raise ResearchError('invalid_review_route', 'Use codex_cli_v1 at codex://local')
    config = route['configuration']
    if set(config) != {'max_output_tokens', 'timeout_seconds'}:
        raise ResearchError('invalid_review_route', 'Configure max_output_tokens and timeout_seconds')
    if type(config['max_output_tokens']) is not int or config['max_output_tokens'] < 1:
        raise ResearchError('invalid_review_route', 'The observed output-token bound must be positive')
    if type(config['timeout_seconds']) not in (int, float) or not 0 < config['timeout_seconds'] <= 600:
        raise ResearchError('invalid_review_route', 'The execution timeout must be positive and at most 600 seconds')


def packet_text(request):
    for item in request['input']:
        if not isinstance(item.get('content'), str):
            raise ResearchError('review_format_unsupported', 'The Codex reviewer accepts text evidence only; use the Responses route for binary evidence')
    return json.dumps({'review_messages': request['input']}, sort_keys=True, ensure_ascii=False)


def _message_text(item):
    content = item.get('content', [])
    if not isinstance(content, list) or any(not isinstance(part, dict) or
            part.get('type') not in ('input_text', 'output_text') or not isinstance(part.get('text'), str)
            for part in content):
        raise ValueError('Uninspected message content')
    return '\n'.join(part['text'] for part in content)


def _native_digest(text, directory, *, environment=False):
    # macOS reports the physical /private/var path when TMPDIR uses /var.
    # Both names identify this same invocation directory, not extra context.
    value = text.replace(str(Path(directory).resolve()), '{WORKSPACE}')
    value = value.replace(directory, '{WORKSPACE}').replace(str(Path.home()), '{HOME}')
    if environment:
        value = re.sub(r'<current_date>\d{4}-\d{2}-\d{2}</current_date>', '<current_date>{DATE}</current_date>', value)
        value = re.sub(r'<timezone>[A-Za-z0-9_+\-/]+</timezone>', '<timezone>{TIMEZONE}</timezone>', value)
    return hashlib.sha256(value.encode()).hexdigest()


def inspect_rollout(events, request, thread_id, directory, excluded_controls):
    """Inspect actual delivered messages; never use the reviewer's self-report."""
    meta = [event['payload'] for event in events if event.get('type') == 'session_meta']
    expected = packet_text(request)
    issues, users, developer_digests, environment_digests, outputs = [], [], [], [], []
    packet_seen = False
    if (len(meta) != 1 or meta[0].get('id') != thread_id or meta[0].get('source') != 'exec'
            or meta[0].get('base_instructions', {}).get('text') != request['instructions']
            or meta[0].get('forked_from_id') or meta[0].get('parent_thread_id')):
        issues.append('session_identity_or_instructions')
    for event in events:
        kind, item = event.get('type'), event.get('payload', {})
        if kind == 'compacted':
            issues.append('uninspected_compaction')
        if kind != 'response_item':
            continue
        if any(control in json.dumps(item) for control in excluded_controls):
            issues.append('excluded_control_exposed')
        if item.get('type') not in ('message', 'reasoning'):
            issues.append('unknown_or_tool_input')
            continue
        if item.get('type') == 'reasoning':
            if not packet_seen:
                issues.append('inherited_reasoning')
            continue
        if item.get('role') not in ('developer', 'user', 'assistant'):
            issues.append('unknown_input_role')
        try:
            text = _message_text(item)
        except ValueError:
            issues.append('unknown_message_content')
            continue
        if item.get('role') == 'developer':
            if packet_seen:
                issues.append('late_developer_input')
            developer_digests.append(_native_digest(text, directory))
        if item.get('role') == 'assistant':
            if not packet_seen:
                issues.append('inherited_assistant_history')
            outputs.append(text)
        if item.get('role') == 'user':
            if text.startswith('<environment_context>') and text.endswith('</environment_context>'):
                fingerprint = _native_digest(text, directory, environment=True)
                environment_digests.append(fingerprint)
                if fingerprint not in NATIVE_ENVIRONMENTS:
                    issues.append('unexpected_environment')
            else:
                users.append(text)
                packet_seen = text == expected
    if tuple(developer_digests) != NATIVE_DECLARATIONS:
        issues.append('unknown_developer_input')
    if len(environment_digests) > 1:
        issues.append('repeated_environment_input')
    if users != [expected]:
        issues.append('packet_or_history_mismatch')
    return {'input_verified': not issues, 'issues': sorted(set(issues)),
            'request_digest': digest(request), 'packet_digest': digest(expected),
            'rollout_digest': digest(events), 'thread_id': thread_id,
            'developer_digests': developer_digests, 'excluded_controls': list(excluded_controls),
            'environment_digests': environment_digests,
            'assistant_output_digest': digest(outputs),
            'observed_events': len(events), 'input_contract': 'observed-exec-rollout-v1'}


def _rollout(thread_id, environment):
    home = Path(environment.get('CODEX_HOME', str(Path.home() / '.codex')))
    matches = list((home / 'sessions').rglob('*' + thread_id + '*.jsonl'))
    if len(matches) != 1:
        raise ResearchError('review_context_unverified', 'The actual reviewer input record is unavailable or ambiguous')
    return [json.loads(line) for line in matches[0].read_text().splitlines() if line.strip()]


def send_request(route, request, credential=None):
    validate_route(route)
    packet = packet_text(request)
    environment = clean_environment(os.environ)
    actual_runtime = runtime()
    # A synthetic project instruction is deliberately reachable in this working
    # directory but must be absent from the actual model input.
    control = 'EXCLUDED_PROJECT_' + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix='review-context-', dir=environment.get('TMPDIR')) as directory:
        root = Path(directory)
        (root / 'AGENTS.md').write_text('This excluded project instruction contains ' + control + '.\n')
        instructions = root / 'review-instructions.txt'
        instructions.write_text(request['instructions'])
        command = [shutil.which('codex'), 'exec', '--ignore-user-config', '--ignore-rules',
                   '--skip-git-repo-check', '--json', '--color', 'never', '-s', 'read-only',
                   '-m', route['model']]
        for value in CONTROLS + ('model_instructions_file=' + json.dumps(str(instructions)),):
            command.extend(('-c', value))
        command.append('-')
        result = _run_owned(command, packet=packet, directory=directory, environment=environment,
                            timeout=route['configuration']['timeout_seconds'])
        raw, returncode = result.stdout, result.returncode
        event_log, malformed = [], []
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError('Event is not an object')
                event_log.append(event)
            except ValueError:
                malformed.append(hashlib.sha256(line.encode()).hexdigest())
        threads = [event['thread_id'] for event in event_log if event.get('type') == 'thread.started']
        thread_id = threads[0] if len(threads) == 1 else None
        messages = [event['item']['text'] for event in event_log if event.get('type') == 'item.completed'
                    and event.get('item', {}).get('type') == 'agent_message']
        usages = [event['usage'] for event in event_log if event.get('type') == 'turn.completed']
        usage = usages[-1] if usages else {}
        evidence = {'input_verified': False, 'issues': ['input_record_unavailable']}
        if thread_id:
            try:
                evidence = inspect_rollout(_rollout(thread_id, environment), request, thread_id, directory, [control])
            except (OSError, ValueError, ResearchError):
                pass
        evidence.update(runtime=actual_runtime, route_digest=digest(route), event_digest=digest(event_log),
                        request_digest=digest(request), inspected=True)
        if evidence.get('assistant_output_digest') != digest(messages):
            evidence['input_verified'] = False
            evidence['issues'].append('provider_output_mismatch')
        complete = (returncode == 0 and not malformed and len(messages) == 1 and len(usages) == 1
                    and usage.get('output_tokens', float('inf')) <= route['configuration']['max_output_tokens'])
        return {'id': thread_id or 'failed-' + uuid.uuid4().hex, 'model': route['model'],
                'status': 'completed' if complete else 'incomplete',
                'output': [{'type': 'message', 'role': 'assistant',
                            'content': [{'type': 'output_text', 'text': messages[-1] if messages else ''}]}],
                'usage': usage, 'context_evidence': evidence,
                'execution': {'returncode': returncode, 'timeout': returncode is None,
                              'stdout_digest': hashlib.sha256(raw.encode()).hexdigest(),
                              'malformed_line_digests': malformed,
                              'event_count': len(event_log), 'event_types': [event.get('type') for event in event_log]}}


def verify_context(route, request, response):
    evidence = response.get('context_evidence', {})
    return (evidence.get('inspected') is True and evidence.get('input_verified') is True
            and evidence.get('request_digest') == digest(request)
            and evidence.get('route_digest') == digest(route)
            and evidence.get('input_contract') == 'observed-exec-rollout-v1'
            and bool(evidence.get('rollout_digest')) and bool(evidence.get('event_digest'))
            and evidence.get('thread_id') == response.get('id')
            and evidence.get('runtime') == runtime())
