"""Classify nonexecuting exec calls without changing the route's pinned transport."""

import os

from . import review_codex_transport as codex
from .errors import ResearchError
from .evidence import digest


def _inspect_disabled_exec_calls(events):
    pending, seen, notices = {}, set(), []
    for index, event in enumerate(events):
        if event.get('type') != 'response_item':
            continue
        item = event.get('payload', {})
        kind = item.get('type')
        if kind == 'custom_tool_call':
            call_id = item.get('call_id')
            if (item.get('name') != 'exec' or not isinstance(call_id, str)
                    or not call_id or call_id in seen):
                return []
            seen.add(call_id)
            pending[call_id] = index
            notices.append({'event_index': index, 'event': item})
        elif kind == 'custom_tool_call_output':
            call_id = item.get('call_id')
            if (not isinstance(call_id, str) or call_id not in pending
                    or item.get('output') != 'code-mode host is disabled'):
                return []
            del pending[call_id]
        elif kind not in ('message', 'reasoning'):
            return []
    return notices if not pending else []


def record_disabled_exec_notices(route, response):
    if route['adapter'] not in ('codex_cli_v1', 'codex_cli_framed_v1', 'codex_cli_visual_v1') or response is None:
        return
    evidence = response.get('context_evidence', {})
    if evidence.get('inspected') is not True or 'tool_call:exec' not in evidence.get('issues', []):
        return
    reader = codex
    if route['adapter'] == 'codex_cli_framed_v1':
        from . import review_codex_framed_transport
        reader = review_codex_framed_transport
    try:
        events = reader._rollout(response['id'], reader.clean_environment(os.environ))
        if digest(events) != evidence.get('rollout_digest') or len(events) != evidence.get('observed_events'):
            return
        notices = _inspect_disabled_exec_calls(events)
    except (OSError, ValueError, TypeError, KeyError, ResearchError):
        return
    if not notices:
        return
    issues = [issue for issue in evidence['issues'] if issue not in ('tool_call:exec', 'unknown_or_tool_input')]
    response['context_evidence'] = dict(evidence, input_verified=not issues, issues=issues,
                                        notices=evidence.get('notices', []) + notices)
