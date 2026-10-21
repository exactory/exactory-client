"""Stateless reviewer transport with a complete, instrumented input boundary.

Only explicitly supplied packet bytes and canonical instructions reach the API.
The transport never loads local agent configuration, memory, history, or tools.
Credentials are a separate argument and are never placed in a saved receipt.
"""

import contextlib
import hashlib
import http.client
import importlib
import json
import os
import platform
import ssl
import time
from pathlib import Path
from urllib.parse import urlsplit

from .errors import ResearchError
from .evidence import digest

ADAPTER = 'openai_responses_v1'
EXCLUDED_SOURCES = ('author_history', 'prior_assessments', 'startup_memory',
                    'project_instructions', 'hooks', 'shared_cache', 'tool_context', 'fork_resume')
# Local reviewer adapters run a subscription CLI of the host and inspect its observed input. The Claude Code adapter is
# the reviewer of a Claude Code session; the Codex adapter is the reviewer of a Codex session.
LOCAL_ADAPTERS = {'claude_cli_v1': 'review_claude_transport', 'codex_cli_v1': 'review_codex_transport'}
# Both local adapters carry text only: a binary artifact is named in the packet without its bytes.
TEXT_ONLY_ADAPTERS = frozenset(LOCAL_ADAPTERS)
TEXT_ONLY_DELIVERY_VARIABLE = 'EXACTORY_REVIEW_TEXT_ONLY_DELIVERY'


def text_only_enabled():
    """Whether packets are being built for a text-only route."""
    return os.environ.get(TEXT_ONLY_DELIVERY_VARIABLE) == '1'


@contextlib.contextmanager
def text_only_delivery(enabled):
    """Build packets with text-only source deliveries while enabled: binary originals are named without their bytes."""
    previous = os.environ.get(TEXT_ONLY_DELIVERY_VARIABLE)
    if enabled:
        os.environ[TEXT_ONLY_DELIVERY_VARIABLE] = '1'
    try:
        yield
    finally:
        if enabled:
            if previous is None:
                os.environ.pop(TEXT_ONLY_DELIVERY_VARIABLE, None)
            else:
                os.environ[TEXT_ONLY_DELIVERY_VARIABLE] = previous
OMITTED_BYTES_NOTE = ('The original bytes of this artifact are not delivered on the text-only reviewer route. When the '
                      'harness captured an extraction of a full text, its text is a separate evidence entry.')


def _local_adapter(route):
    """The module of a local reviewer adapter, or None for the HTTPS Responses adapter."""
    name = LOCAL_ADAPTERS.get(route['adapter'])
    return None if name is None else importlib.import_module('.' + name, __package__)


def runtime(route=None):
    local = None if not route else _local_adapter(route)
    if local is not None:
        return local.runtime()
    return {'adapter': ADAPTER, 'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'host': platform.node(), 'platform': platform.platform(), 'python': platform.python_version(),
            'input_contract': 'explicit-packet-only-v1'}


def validate_route(route):
    local = _local_adapter(route)
    if local is not None:
        return local.validate_route(route)
    if route['adapter'] != ADAPTER:
        raise ResearchError('review_route_unsupported', 'Only the instrumented stateless Responses adapter can execute')
    url = urlsplit(route['endpoint'])
    if (url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment):
        raise ResearchError('invalid_review_route', 'The reviewer endpoint must be an explicit HTTPS URL without credentials')
    config = route['configuration']
    if set(config) != {'max_output_tokens', 'timeout_seconds'}:
        raise ResearchError('invalid_review_route', 'Configure only max_output_tokens and timeout_seconds')
    if type(config['max_output_tokens']) is not int or config['max_output_tokens'] < 1:
        raise ResearchError('invalid_review_route', 'max_output_tokens must be positive')
    if type(config['timeout_seconds']) not in (int, float) or not 0 < config['timeout_seconds'] <= 600:
        raise ResearchError('invalid_review_route', 'timeout_seconds must be positive and at most 600')


CODEX_INPUT_LIMIT_CHARS = 1048576
PACKET_LABEL = 'The packet below is JSON.\n'
ELIDED_TEXT_NOTE = ('The body of this full text is withheld because the reviewer route input limit does not admit '
                    'it; the reading record of this work, with its notes and inspected excerpts, is delivered in full, '
                    'and the artifact reference identifies the exact bytes.')
ELIDED_BODY_NOTE = ('This evidence body was delivered in this earlier turn of the same review. It is elided from '
                    'the replayed history so that the exchange fits the reviewer route input limit; request the '
                    'artifact again by its reference if the review needs its text.')
# A packet whose history_delivery names this value replays its session's earlier turns without long evidence
# bodies and without the packets of a scientific history; a packet without a marker replays unchanged.
HISTORY_COMPACT = 'earlier-bodies-and-history-packets-elided'
HISTORY_PACKET_NOTE = ('This packet of an earlier turn was delivered in that turn of the same review session. It is '
                       'elided from the replayed history; the observed output of that turn is kept.')
HISTORY_PACKET_LIMIT_NOTE = ('This packet of an earlier turn of this slot exceeds the reviewer route input limit together with '
                             'the current packet. Its observed output and independence are kept here, and the record '
                             'holds the packet at this digest.')
HISTORY_BODY_NOTE = ('This evidence body was delivered in this earlier turn of the same review and is omitted from the '
                     'replay. The review is verified only when it calls no tool, so request its work with '
                     'source_requests if the review needs this text again.')
# The second marker also drops, from each replayed turn, a part that the current packet repeats unchanged and
# summarizes each replayed source delivery to its request and status. Packets with the first marker replay as before.
HISTORY_COMPACT_V2 = 'earlier-bodies-history-packets-and-repeated-parts-elided'
REPEATABLE_KEYS = ('bars', 'own_bars', 'material_objections', 'source_inventory', 'problem_evidence')
REPEATED_PART_NOTE = 'This part of the earlier packet is repeated unchanged in the current packet.'
DELIVERY_SUMMARY_NOTE = ('This source delivery of an earlier turn is summarized in the replay: its request and status are '
                         'kept, and its evidence was delivered in that turn.')
REPEATED_EVIDENCE_NOTE = ('This problem evidence was delivered in full in an earlier turn of this review session, and the '
                          'replayed history carries that packet.')


def _elide_history_bodies(message):
    """An earlier user turn without the bodies of its evidence, stating the omission."""
    content = message.get('content')
    if message.get('role') != 'user' or not isinstance(content, str) or not content.startswith(PACKET_LABEL):
        return message
    replayed = json.loads(content[len(PACKET_LABEL):])
    changed = False
    for evidence in replayed.get('evidence', []):
        if isinstance(evidence, dict) and isinstance(evidence.get('content'), str) and len(evidence['content']) > 4096:
            evidence['content'] = ''
            evidence['delivery'] = 'body-elided-from-replayed-history'
            evidence['note'] = ELIDED_BODY_NOTE
            changed = True
    if not changed:
        return message
    return dict(message, content=PACKET_LABEL + json.dumps(replayed, sort_keys=True, ensure_ascii=False))


def _elide_body(evidence, delivery, note):
    if isinstance(evidence, dict) and isinstance(evidence.get('content'), str) and len(evidence['content']) > 4096:
        evidence['content'] = ''
        evidence['delivery'] = delivery
        evidence['note'] = note
        return True
    return False


def _compact_history_message(message, repeated=None):
    """An earlier user turn without its evidence bodies longer than 4096 characters and without the packets of the
    scientific history that it carried; with `repeated`, also without the parts the current packet repeats."""
    content = message.get('content')
    if message.get('role') != 'user' or not isinstance(content, str) or not content.startswith(PACKET_LABEL):
        return message
    replayed = json.loads(content[len(PACKET_LABEL):])
    changed = False
    for evidence in replayed.get('evidence', []):
        changed = _elide_body(evidence, 'body-elided-from-replayed-history', HISTORY_BODY_NOTE) or changed
    for turn in replayed.get('scientific_history', []):
        if isinstance(turn, dict) and isinstance(turn.get('packet'), dict):
            turn['packet'] = {'delivery': 'packet-elided-from-replayed-history',
                              'packet_digest': turn['packet'].get('digest'), 'note': HISTORY_PACKET_NOTE}
            changed = True
    for key, value in (repeated or {}).items():
        if key in replayed and replayed[key] == value:
            replayed[key] = {'delivery': 'repeated-in-the-current-packet', 'note': REPEATED_PART_NOTE}
            changed = True
    if repeated is not None:
        # With the second marker a replayed source delivery keeps its request and status.
        deliveries = replayed.get('source_deliveries')
        if isinstance(deliveries, list) and deliveries:
            replayed['source_deliveries'] = [
                {'request': item.get('request'), 'status': item.get('status'),
                 'delivery': 'summarized-in-replayed-history', 'note': DELIVERY_SUMMARY_NOTE}
                if isinstance(item, dict) else item for item in deliveries]
            changed = True
    if not changed:
        return message
    return dict(message, content=PACKET_LABEL + json.dumps(replayed, sort_keys=True, ensure_ascii=False))


def _elide_packet_bodies(supplied):
    """Deterministic reductions of a packet that re-delivers earlier turns' sources (a context-repair packet) or
    declares a delivery priority of its full texts. Each is applied only while the input is too large."""
    for turn in supplied.get('scientific_history', []):
        for evidence in (turn.get('packet') or {}).get('evidence', []):
            _elide_body(evidence, 'body-elided-from-replayed-history', ELIDED_BODY_NOTE)
    yield 'scientific_history'
    supplied_before = {entry.get('sha256') for entry in supplied.get('previously_supplied_evidence', []) if isinstance(entry, dict)}
    for evidence in supplied.get('evidence', []):
        if isinstance(evidence, dict) and (evidence.get('artifact') or {}).get('sha256') in supplied_before:
            _elide_body(evidence, 'body-elided-previously-supplied', ELIDED_BODY_NOTE)
    yield 'previously_supplied_evidence'
    # A native packet may declare the delivery priority of its full texts; bodies are elided from the lowest
    # priority upwards, one per step.
    priority = ((supplied.get('native') or {}).get('scientific_context') or {}).get('text_delivery_priority') or []
    entries = {(evidence.get('artifact') or {}).get('sha256'): evidence
               for evidence in supplied.get('evidence', []) if isinstance(evidence, dict)}
    for sha256 in reversed(priority):
        if sha256 in entries and _elide_body(entries[sha256], 'body-elided-route-limit', ELIDED_TEXT_NOTE):
            yield 'text_delivery_priority:' + sha256
    # The earlier packets of the slot's history, largest first, give way to a digest reference while the input is
    # still too large; each turn keeps its observed output and its independence.
    turns = [turn for turn in supplied.get('scientific_history', []) if isinstance(turn, dict) and isinstance(turn.get('packet'), dict)]
    for turn in sorted(turns, key=lambda turn: -len(json.dumps(turn['packet'], sort_keys=True, ensure_ascii=False))):
        turn['packet'] = {'delivery': 'packet-elided-route-limit', 'packet_digest': turn['packet'].get('digest'),
                          'note': HISTORY_PACKET_LIMIT_NOTE}
        yield 'scientific_history_packet:' + str(turn.get('assignment_id'))


def _build_local_request(route, prompt, supplied, messages):
    content = PACKET_LABEL + json.dumps(supplied, sort_keys=True, ensure_ascii=False)
    return {'model': route['model'], 'instructions': prompt,
            'input': list(messages) + [{'role': 'user', 'content': content}],
            'tools': [], 'store': False, 'max_output_tokens': route['configuration']['max_output_tokens'],
            'text': {'format': {'type': 'json_object'}}}


def _compact_history(supplied, history):
    """The replayed earlier turns of a session, compacted as the packet's history marker asks."""
    messages = list(history)
    marker = supplied.get('history_delivery')
    if marker in (HISTORY_COMPACT, HISTORY_COMPACT_V2):
        repeated = ({key: supplied[key] for key in REPEATABLE_KEYS if key in supplied}
                    if marker == HISTORY_COMPACT_V2 else None)
        messages = [_compact_history_message(message, repeated) for message in messages]
    return messages


def _fit_local_input(route, prompt, supplied, history, limit, measure):
    """Elide re-delivered and replayed evidence bodies until the input fits the route limit. An input that no elision
    fits is sent as it stands: the provider refuses it, and the refusal is recorded as a failed attempt."""
    def fits(request):
        return len(measure(request)) <= limit

    messages = _compact_history(supplied, history)
    request = _build_local_request(route, prompt, supplied, messages)
    reductions = _elide_packet_bodies(supplied)
    while not fits(request):
        if next(reductions, None) is None:
            break
        request = _build_local_request(route, prompt, supplied, messages)
    index = 0
    while not fits(request) and index < len(messages):
        messages[index] = _elide_history_bodies(messages[index])
        request = _build_local_request(route, prompt, supplied, messages)
        index += 1
    return request


def _fit_codex_input(route, prompt, supplied, history):
    """The Codex app-server admits 1,048,576 characters of input."""
    from . import review_codex_transport
    return _fit_local_input(route, prompt, supplied, history, CODEX_INPUT_LIMIT_CHARS, review_codex_transport.packet_text)


def _fit_claude_input(route, prompt, supplied, history):
    """The Claude Code route admits its own limit of characters; the adapter refuses a larger input and records it."""
    from . import review_claude_transport
    return _fit_local_input(route, prompt, supplied, history, review_claude_transport.INPUT_LIMIT_CHARACTERS,
                            review_claude_transport.packet_text)


def request_body(route, prompt, packet, history=()):
    """History contains only the same reviewer's verified, canonical bar exchange."""
    validate_route(route)
    supplied = json.loads(json.dumps(packet))
    files = []
    for evidence in supplied.get('evidence', []):
        if not isinstance(evidence, dict) or evidence.get('encoding') != 'base64':
            continue
        reference = evidence['artifact']
        if route['adapter'] in TEXT_ONLY_ADAPTERS:
            # A text-only local route carries no original bytes; the omission is stated in the packet.
            evidence.pop('content')
            evidence['encoding'] = 'omitted'
            evidence['delivery'] = 'bytes-omitted-on-text-route'
            evidence['note'] = OMITTED_BYTES_NOTE
            continue
        data = 'data:' + reference['media_type'] + ';base64,' + evidence.pop('content')
        if reference['media_type'] == 'application/pdf':
            files.append({'type': 'input_file', 'filename': reference['sha256'] + '.pdf', 'file_data': data})
        else:
            files.append({'type': 'input_image', 'image_url': data})
        evidence['delivery'] = 'attached-exact-bytes'
    # The Responses API refuses text.format json_object unless the input messages
    # contain the word 'json'; the packet is labelled as JSON for that reason.
    content = PACKET_LABEL + json.dumps(supplied, sort_keys=True, ensure_ascii=False)
    if files:
        content = [{'type': 'input_text', 'text': content}] + files
    request = {'model': route['model'], 'instructions': prompt,
               'input': list(history) + [{'role': 'user', 'content': content}],
               'tools': [], 'store': False, 'max_output_tokens': route['configuration']['max_output_tokens'],
               'text': {'format': {'type': 'json_object'}}}
    if route['adapter'] == 'codex_cli_v1':
        return _fit_codex_input(route, prompt, supplied, history)
    if route['adapter'] == 'claude_cli_v1':
        return _fit_claude_input(route, prompt, supplied, history)
    return request


def send_request(route, request, credential):
    """Send once without redirects, implicit proxy configuration, or retries."""
    local = _local_adapter(route)
    if local is not None:
        return local.send_request(route, request, credential)
    if not isinstance(credential, str) or not credential:
        raise ResearchError('review_credential_missing', 'An explicit provider credential is required')
    validate_route(route)
    url = urlsplit(route['endpoint'])
    connection = http.client.HTTPSConnection(url.hostname, url.port, timeout=route['configuration']['timeout_seconds'],
                                            context=ssl.create_default_context())
    try:
        body = json.dumps(request, ensure_ascii=False, allow_nan=False).encode()
        connection.request('POST', url.path or '/', body=body,
                           headers={'Authorization': 'Bearer ' + credential, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        data = response.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024:
            raise ResearchError('review_response_too_large', 'The provider response exceeds 16 MiB')
        if not 200 <= response.status < 300:
            raise ResearchError('review_provider_failure', 'The provider returned HTTP ' + str(response.status))
        return json.loads(data)
    finally:
        connection.close()



def context_verified(route, request, response):
    local = _local_adapter(route)
    if local is not None:
        return local.verify_context(route, request, response)
    return (route['adapter'] == ADAPTER and request.get('tools') == [] and request.get('store') is False
            and 'previous_response_id' not in request and 'conversation' not in request)


def response_output(response):
    parts = [part['text'] for item in response.get('output', []) if item.get('type') == 'message'
             for part in item.get('content', []) if part.get('type') == 'output_text']
    output = json.loads(''.join(parts))
    if response.get('status') != 'completed' or not isinstance(output, dict) or not response.get('id'):
        raise ResearchError('review_incomplete', 'The provider did not return a complete JSON object')
    return output


def observe(route, request, credential):
    """Preserve operational failure and unknown usage instead of inventing a verdict."""
    started = time.monotonic()
    result = {'request_digest': digest(request), 'route_digest': digest(route), 'runtime': runtime(route),
              'response': None, 'output': None, 'status': 'failed', 'error': None,
              'usage': {'input_tokens': None, 'output_tokens': None, 'cost_usd': None, 'wall_seconds': None}}
    try:
        response = send_request(route, request, credential)
        result['response'] = response
        usage = response.get('usage') or {}
        for key in ('input_tokens', 'output_tokens'):
            count = usage.get(key)
            if type(count) is int and count >= 0:
                result['usage'][key] = count
        output = response_output(response)
        result.update(status='completed', output=output, provider_response_id=response['id'],
                      observed_model=response.get('model'))
    except (OSError, ValueError, TypeError, KeyError, ResearchError) as error:
        result['error'] = {'kind': type(error).__name__, 'code': getattr(error, 'code', 'review_transport_failure')}
    result['usage']['wall_seconds'] = time.monotonic() - started
    return result
