"""Stateless reviewer transport with a complete, instrumented input boundary.

Only explicitly supplied packet bytes and canonical instructions reach the API.
The transport never loads local agent configuration, memory, history, or tools.
Credentials are a separate argument and are never placed in a saved receipt.
"""

import hashlib
import http.client
import json
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


def runtime(route=None):
    if route and route['adapter'] == 'codex_cli_v1':
        from . import review_codex_transport
        return review_codex_transport.runtime()
    return {'adapter': ADAPTER, 'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'host': platform.node(), 'platform': platform.platform(), 'python': platform.python_version(),
            'input_contract': 'explicit-packet-only-v1'}


def validate_route(route):
    if route['adapter'] == 'codex_cli_v1':
        from . import review_codex_transport
        return review_codex_transport.validate_route(route)
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


def request_body(route, prompt, packet, history=()):
    """History contains only the same reviewer's verified, canonical bar exchange."""
    validate_route(route)
    supplied = json.loads(json.dumps(packet))
    files = []
    for evidence in supplied.get('evidence', []):
        if not isinstance(evidence, dict) or evidence.get('encoding') != 'base64':
            continue
        reference = evidence['artifact']
        data = 'data:' + reference['media_type'] + ';base64,' + evidence.pop('content')
        if reference['media_type'] == 'application/pdf':
            files.append({'type': 'input_file', 'filename': reference['sha256'] + '.pdf', 'file_data': data})
        else:
            files.append({'type': 'input_image', 'image_url': data})
        evidence['delivery'] = 'attached-exact-bytes'
    content = json.dumps(supplied, sort_keys=True, ensure_ascii=False)
    if files:
        content = [{'type': 'input_text', 'text': content}] + files
    return {'model': route['model'], 'instructions': prompt,
            'input': list(history) + [{'role': 'user', 'content': content}],
            'tools': [], 'store': False, 'max_output_tokens': route['configuration']['max_output_tokens'],
            'text': {'format': {'type': 'json_object'}}}


def send_request(route, request, credential):
    """Send once without redirects, implicit proxy configuration, or retries."""
    if route['adapter'] == 'codex_cli_v1':
        from . import review_codex_transport
        return review_codex_transport.send_request(route, request, credential)
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
    if route['adapter'] == 'codex_cli_v1':
        from . import review_codex_transport
        return review_codex_transport.verify_context(route, request, response)
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
