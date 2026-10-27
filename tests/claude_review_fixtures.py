"""Synthetic bindings of the recorded evidence shapes documented by the two 2026-10-25 incidents.

The implementation digests are SHA-256 of the adapter at the release tags and draft commits. No historical
stream is available; these fixtures deliberately contain only the evidence those adapters retained.
"""

import json

from research_harness.evidence import digest


RELEASED_IMPLEMENTATION = '7b8facd0f6f679dee371b9f0b46ca0d6b2ad799ea0dfec9efe215d95402a4800'
DRAFT_V2_IMPLEMENTATION = '3f933eff2d57697204a70cbaffb402072a5486f39e25a34fa43f1a2762fa4927'
DRAFT_V3_IMPLEMENTATION = 'f19ad2f1b07043e745b48426fd2ca10f23eaf1c181eae7137dfce0be50bc04a5'
RECORDED_CONTEXTS = (
    ('released-0.60.0-to-0.63.0', RELEASED_IMPLEMENTATION, True, [], None),
    ('trianglefree-route-v2', DRAFT_V2_IMPLEMENTATION, True, [], [
        {'event_index': 3, 'event': {'type': 'system', 'subtype': 'ui_invalidate', 'event': 'ui.render',
                                  'uuid': 'recorded-notice', 'session_id': 'recorded-session'}}]),
    ('trianglefree-route-v3', DRAFT_V3_IMPLEMENTATION, True, [], [
        {'event_index': 3, 'event': {'type': 'system', 'subtype': 'ui_invalidate', 'event': 'ui.render',
                                  'uuid': 'recorded-notice', 'session_id': 'recorded-session'}}]),
    ('ferrers-local-repair', RELEASED_IMPLEMENTATION, False, ['system_event_ui_invalidate'], None),
)


def build_recorded_runtime(implementation):
    return {'adapter': 'claude_cli_v1',
            'flags': ['-p', '--safe-mode', '--tools', '', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                      '--disable-slash-commands', '--no-session-persistence', '--permission-prompts', 'none',
                      '--input-format', 'stream-json', '--replay-user-messages', '--output-format',
                      'stream-json', '--verbose'],
            'input_contract': 'observed-print-stream-v1', 'implementation_sha256': implementation,
            'credential_mode': 'claude-subscription',
            'output_limit': 'CLAUDE_CODE_MAX_OUTPUT_TOKENS=max_output_tokens, checked after generation',
            'wall_limit': 'owned-process-group-timeout'}


def build_recorded_response(route, request, output, context):
    name, implementation, verified, issues, notices = context
    evidence = {'inspected': True, 'input_verified': verified, 'issues': list(issues),
                'request_digest': digest(request), 'route_digest': digest(route),
                'input_contract': 'observed-print-stream-v1', 'event_digest': digest('unavailable-stream-' + name),
                'session_id': 'recorded-session', 'runtime': build_recorded_runtime(implementation),
                'cli_version': '2.1.310 (Claude Code)'}
    if notices is not None:
        evidence['notices'] = notices
    return {'id': 'recorded-session', 'model': route['model'], 'status': 'completed',
            'output': [{'type': 'message', 'role': 'assistant',
                        'content': [{'type': 'output_text', 'text': json.dumps(output)}]}],
            'usage': {'input_tokens': 1540, 'output_tokens': 900, 'cost_usd_estimate': 0.25},
            'context_evidence': evidence, 'execution': {'event_count': 5, 'event_types': [
                'system', 'user', 'assistant', 'system', 'result']}}
