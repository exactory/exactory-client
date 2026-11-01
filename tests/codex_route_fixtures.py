"""A codex_cli_v1 review route whose observed input the tests build with the adapter's own rollout inspection."""

import json
from unittest.mock import patch

from research_harness import review_codex_transport as codex, review_protocol as protocol
from research_harness import review_transport as transport
from research_harness.evidence import digest


def install_codex_runtime(case):
    for target, value in (('runtime', lambda: {'adapter': 'codex_cli_v1', 'fixture': 'reviewer-excerpt-test'}),
                          ('NATIVE_DECLARATIONS', ()), ('NATIVE_DECLARATION_SETS', {()})):
        patcher = patch.object(codex, target, value)
        patcher.start()
        case.addCleanup(patcher.stop)


def build_codex_response(route, request, output):
    response = {'id': 'provider-response', 'model': 'fixture-model', 'status': 'completed',
                'output': [{'type': 'message', 'role': 'assistant',
                            'content': [{'type': 'output_text', 'text': json.dumps(output)}]}],
                'usage': {'input_tokens': 37, 'output_tokens': 12}}
    events = [{'type': 'session_meta', 'payload': {'id': response['id'], 'source': 'exec',
               'base_instructions': {'text': request['instructions']}}},
              {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
               'content': [{'type': 'input_text', 'text': codex.packet_text(request)}]}}]
    evidence = codex.inspect_rollout(events, request, response['id'], '/fixture', [])
    evidence.update(inspected=True, route_digest=digest(route), runtime=codex.runtime(), event_digest=digest(events))
    response['context_evidence'] = evidence
    return response


def record_codex_route(case, identifier='route', *, output_tokens=1000, timeout_seconds=5):
    """Register and probe a Codex route in the case's store."""
    install_codex_runtime(case)
    protocol.record_route(case.store, {'id': identifier, 'adapter': 'codex_cli_v1', 'model': 'fixture-model',
        'endpoint': 'codex://local', 'configuration': {'max_output_tokens': output_tokens, 'timeout_seconds': timeout_seconds}},
        expected_revision=case.store.revision, request_id='codex-route-' + identifier)
    probe_codex_route(case, 'probe-' + identifier, identifier)


def probe_codex_route(case, probe_id, route_id='route'):
    def respond_to_probe(route, request, credential):
        packet = json.loads(request['input'][0]['content'][len(transport.PACKET_LABEL):])
        return build_codex_response(route, request, {'allowed_control': packet['allowed_control'],
                                                     'evidence_control': packet['evidence_control'],
                                                     'excluded_controls': []})
    with patch.object(transport, 'send_request', side_effect=respond_to_probe):
        protocol.probe_route(case.store, {'id': probe_id, 'route_id': route_id}, credential='test-only',
                             expected_revision=case.store.revision, request_id='codex-probe-' + probe_id)


def invoke_codex(case, assignment_id, output, attempt_id=None):
    attempt_id = attempt_id or 'attempt-' + assignment_id
    with patch.object(transport, 'send_request',
                      side_effect=lambda route, request, credential: build_codex_response(route, request, output)):
        return protocol.invoke_assignment(case.store, {'id': attempt_id, 'assignment_id': assignment_id},
                                          credential='test-only', expected_revision=case.store.revision,
                                          request_id='invoke-' + attempt_id)


def limit_codex_input(case, characters):
    patcher = patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', characters)
    patcher.start()
    case.addCleanup(patcher.stop)


def read_sent_packet(case, attempt_id):
    """The packet of the current turn as the recorded request of an attempt delivered it."""
    attempt = case.store.snapshot()['records']['review_attempt'][attempt_id]
    request = json.loads(case.artifacts.read(attempt['request']))
    return request, json.loads(request['input'][-1]['content'][len(transport.PACKET_LABEL):])
