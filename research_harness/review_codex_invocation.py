"""Versioned native settings capture around the unchanged Codex senders."""

import copy
import json
import os
from pathlib import Path
import re

from . import review_codex_transport as plain
from . import review_codex_framed_transport as framed
from . import review_transport as transport
from .errors import ResearchError
from .evidence import digest


PROTOCOL = 'codex-invocation-settings-v1'
KEY = 'codex_invocation'
REQUIRED_EFFORT = 'high'


def supports_route(route):
    return route.get('adapter') in (plain.ADAPTER, framed.ADAPTER)


def has_supported_binding(*records):
    """Accept unversioned records and only the exact supported invocation policy."""
    return all(record.get(KEY) in (None, PROTOCOL) for record in records)


def _inspect_capture(route, request, response, events):
    """Rebuild sender inspection and bind native settings to its exact rollout."""
    evidence = response.get('context_evidence')
    if (not isinstance(evidence, dict)
            or not transport.context_verified(route, request, response)
            or request.get('model') != route['model']
            or response.get('model') != route['model']
            or not isinstance(events, list)
            or any(not isinstance(event, dict)
                   or not isinstance(event.get('payload'), dict) for event in events)
            or digest(events) != evidence.get('rollout_digest')):
        return False
    sessions = [event['payload'] for event in events if event.get('type') == 'session_meta']
    if (len(sessions) != 1 or not isinstance(sessions[0].get('base_instructions'), dict)
            or not isinstance(sessions[0]['base_instructions'].get('text'), str)):
        return False
    contexts = [event['payload'] for event in events if event.get('type') == 'turn_context']
    if (len(contexts) != 1 or contexts[0].get('model') != route['model']
            or contexts[0].get('effort') != REQUIRED_EFFORT):
        return False
    directory = contexts[0].get('cwd')
    controls = evidence.get('excluded_controls')
    if (not isinstance(directory, str) or not Path(directory).is_absolute()
            or not isinstance(controls, list)
            or any(not isinstance(control, str) for control in controls)):
        return False
    adapter = plain if route['adapter'] == plain.ADAPTER else framed
    inspection = adapter.inspect_rollout(events, request, response['id'], directory, controls)
    if 'tool_call:exec' in inspection['issues']:
        from .review_codex_notices import _inspect_disabled_exec_calls
        notices = _inspect_disabled_exec_calls(events)
        if notices:
            issues = [issue for issue in inspection['issues']
                      if issue not in ('tool_call:exec', 'unknown_or_tool_input')]
            inspection.update(input_verified=not issues, issues=issues, notices=notices)
    if not inspection['input_verified'] or any(
            evidence.get(key) != value for key, value in inspection.items()):
        return False
    outputs = response.get('output')
    if not isinstance(outputs, list):
        return False
    texts = []
    for output in outputs:
        if (not isinstance(output, dict) or output.get('type') != 'message'
                or output.get('role') != 'assistant'):
            return False
        parts = output.get('content')
        if (not isinstance(parts, list) or len(parts) != 1
                or not isinstance(parts[0], dict) or parts[0].get('type') != 'output_text'
                or not isinstance(parts[0].get('text'), str)):
            return False
        texts.append(parts[0]['text'])
    return digest(texts) == inspection['assistant_output_digest']


def capture_response(route, request, response, events):
    """Own the complete native evidence without changing the sender's evidence."""
    if not supports_route(route):
        return response
    captured = copy.deepcopy(response)
    captured[KEY] = dict(protocol=PROTOCOL, native_rollout=copy.deepcopy(events))
    return captured


def capture_observation(route, request, observation):
    """Retain native settings after the original observed sender has returned."""
    response = observation.get('response')
    if not supports_route(route) or response is None:
        return observation
    captured = copy.deepcopy(observation)
    try:
        thread_id = response.get('id')
        if not isinstance(thread_id, str) or re.fullmatch(r'[A-Za-z0-9-]+', thread_id) is None:
            raise ResearchError('review_context_unverified', 'The native thread identity is invalid')
        adapter = plain if route['adapter'] == plain.ADAPTER else framed
        events = adapter._rollout(thread_id, adapter.clean_environment(os.environ))
    except (ResearchError, OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        captured['response'][KEY] = dict(protocol=PROTOCOL, error=dict(
            code=getattr(error, 'code', 'review_context_unverified'), kind=type(error).__name__))
    else:
        captured['response'] = capture_response(route, request, response, events)
    return captured


def observe(route, request, credential):
    """Keep the original sender and observation behavior, then capture settings."""
    return capture_observation(route, request, transport.observe(route, request, credential))


def verify_context(route, request, response, *, required=False):
    """Authenticate retained native settings, preserving unversioned history."""
    if not supports_route(route):
        return transport.context_verified(route, request, response)
    if KEY not in response:
        return not required and transport.context_verified(route, request, response)
    capture = response[KEY]
    if (not isinstance(capture, dict) or set(capture) != {'protocol', 'native_rollout'}
            or capture.get('protocol') != PROTOCOL):
        return False
    return _inspect_capture(route, request, response, capture['native_rollout'])
