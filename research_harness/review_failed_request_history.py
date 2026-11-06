"""Reconstruct five failed historical forms, without granting authentication.

Only record-check calls this reader, after exhausting ordinary candidates. The
adapter hashes identify supported retained runtimes, not a common-builder commit
for the local routes. No profile is used for dispatch or scientific acceptance.
"""

import base64
import copy
import hashlib
import json

from . import review_protocol as protocol, review_transport as transport
from .evidence import digest


_CODEX_IMPLEMENTATION_SHA256 = 'f2a3919312fba798f79edddc79d0b8e2f15ce62d5b1780244ff07325f62d02a6'
_CLAUDE_IMPLEMENTATION_SHA256 = '7b8facd0f6f679dee371b9f0b46ca0d6b2ad799ea0dfec9efe215d95402a4800'
_UNLABELLED_IMPLEMENTATION_SHA256 = '8755b042a0c0cff46f6f9d118b48b4e806c8350fe9fc939a1737d886d266c443'
_CODEX_RUNTIME = ('codex_cli_v1', 'observed-exec-rollout-v1', _CODEX_IMPLEMENTATION_SHA256)
_PROFILES = (
    ('unlabelled-probe', ('openai_responses_v1', 'explicit-packet-only-v1',
                          _UNLABELLED_IMPLEMENTATION_SHA256)),
    ('labelled-pre-fit-local', _CODEX_RUNTIME),
    ('codex-order-before-source-capacity', _CODEX_RUNTIME),
    ('claude-order-before-full-text-steps', ('claude_cli_v1', 'observed-print-stream-v1',
                                   _CLAUDE_IMPLEMENTATION_SHA256)),
    ('complete-source-exact-text-v1', _CODEX_RUNTIME),
)
# These are the missing prefixes, not new authentication or dispatch fit orders.
_CODEX_ORDER_BEFORE_SOURCE_CAPACITY = (
    'history_bodies', 'scientific_history_bodies', 'html_originals',
    'previously_supplied', 'text_priority',
)
_CLAUDE_ORDER_BEFORE_FULL_TEXT_STEPS = (
    'scientific_history_bodies', 'previously_supplied', 'text_priority',
    'search_responses', 'scientific_history_packets', 'history_bodies', 'history_packets',
)


def _supports_profile(profile, route):
    runtime = route.get('runtime') or {}
    identity = (route['adapter'], runtime.get('input_contract'), runtime.get('implementation_sha256'))
    return runtime.get('adapter') == route['adapter'] and (profile, identity) in _PROFILES


def _build_pre_fit_request(route, prompt, packet, history, *, label):
    """The pre-9fcf478 attachment form, optionally with the later JSON label."""
    supplied = copy.deepcopy(packet)
    files = []
    for evidence in supplied.get('evidence', []):
        if not isinstance(evidence, dict) or evidence.get('encoding') != 'base64':
            continue
        reference = evidence['artifact']
        data_url = 'data:' + reference['media_type'] + ';base64,' + evidence.pop('content')
        if reference['media_type'] == 'application/pdf':
            files.append(dict(type='input_file', filename=reference['sha256'] + '.pdf', file_data=data_url))
        else:
            files.append(dict(type='input_image', image_url=data_url))
        evidence['delivery'] = 'attached-exact-bytes'
    content = label + json.dumps(supplied, sort_keys=True, ensure_ascii=False)
    if files:
        content = [dict(type='input_text', text=content)] + files
    return dict(model=route['model'], instructions=prompt,
                input=list(history) + [dict(role='user', content=content)], tools=[], store=False,
                max_output_tokens=route['configuration']['max_output_tokens'],
                text=dict(format=dict(type='json_object')))


def build_historical_request(profile, route, prompt, packet, history):
    """Independently derive one explicit form from its complete retained inputs."""
    if not _supports_profile(profile, route):
        return None
    from . import review_source_complete as complete
    if profile == 'unlabelled-probe':
        if history or set(packet) != {'allowed_control', 'evidence_control', 'evidence'}:
            return None
        return _build_pre_fit_request(route, prompt, packet, history, label='')
    if profile == 'complete-source-exact-text-v1':
        if complete.MARKER not in packet:
            return None
        from .problem_openness_sources import apply_delivery
        from .review_exact_text import encode
        supplied = copy.deepcopy(packet)
        apply_delivery(supplied)
        for evidence in supplied.get('evidence', []):
            if isinstance(evidence, dict) and evidence.get('encoding') == 'base64':
                evidence.pop('content')
                evidence.update(encoding='omitted', delivery='bytes-omitted-on-text-route',
                                note=transport.OMITTED_BYTES_NOTE)
        messages, sources = complete.project(supplied, history)
        request = transport._build_local_request(route, prompt, supplied, history)
        request['input'] = [dict(role='user', content=encode(messages, sources))]
        return request
    if complete.MARKER in packet:
        return None
    if profile == 'labelled-pre-fit-local':
        return _build_pre_fit_request(route, prompt, packet, history, label=transport.PACKET_LABEL)
    order = (_CODEX_ORDER_BEFORE_SOURCE_CAPACITY if profile == 'codex-order-before-source-capacity'
             else _CLAUDE_ORDER_BEFORE_FULL_TEXT_STEPS)
    return transport.request_body(route, prompt, packet, history, order=order)


def _find_failed_observation(records, invocation, kind, route, recorded):
    """Require a positive same-invocation, failed, explicitly output-null binding."""
    identifier = invocation['id']
    if (records.get(kind, {}).get(identifier) != invocation
            or route.get('id') != invocation.get('route_id')):
        return None
    observation_kind = ('review_route_probe' if kind == 'review_probe_invocation' else 'review_attempt')
    observed = records.get(observation_kind, {}).get(identifier)
    if (observed is None or observed.get('id') != identifier
            or observed.get('status') != 'failed' or 'output' not in observed
            or observed['output'] is not None
            or observed.get('request') != invocation['request']
            or observed.get('request_digest') != digest(recorded)
            or observed.get('route_digest') != digest(route)
            or observed.get('runtime') != route.get('runtime')):
        return None
    if kind == 'review_invocation' and (
            observed.get('origin') != 'instrumented' or observed.get('invocation_id') != identifier
            or observed.get('assignment_id') != invocation['assignment_id']):
        return None
    return observed


def _matches_probe_bindings(invocation, observed, prompt, packet):
    """Check both retained manifests, including the positive controls."""
    if any(observed.get(key) != invocation.get(key) for key in (
            'route_id', 'request', 'controls', 'positive_controls', 'excluded_sources', 'input_manifest')):
        return False
    for record in (invocation, observed):
        manifest = record.get('input_manifest') or {}
        if (manifest.get('instructions') != digest(prompt) or manifest.get('packet') != digest(packet)
                or manifest.get('history') != [] or manifest.get('tools') != []
                or manifest.get('local_context_sources') != []):
            return False
    return True


def _matches_retained_bodies(value):
    """Check visible evidence before fitting can discard its retained body."""
    if isinstance(value, dict):
        content = value.get('content')
        encoding = value.get('encoding')
        if isinstance(content, str) and content and encoding in ('utf-8', 'base64'):
            reference = value.get('artifact') or (
                value.get('artifact_reference_template') or {}).get('artifact_descriptor') or {}
            data = content.encode('utf-8') if encoding == 'utf-8' else base64.b64decode(content, validate=True)
            if len(data) != reference.get('size') or hashlib.sha256(data).hexdigest() != reference.get('sha256'):
                return False
        return all(_matches_retained_bodies(child) for child in value.values())
    if isinstance(value, list):
        return all(_matches_retained_bodies(child) for child in value)
    return True


def _matches_history_bodies(history):
    """Earlier fitted packets keep their original digest and may have empty bodies."""
    for message in history:
        content = message.get('content')
        if message.get('role') != 'user' or not isinstance(content, str):
            continue
        if content.startswith(transport.PACKET_LABEL):
            content = content[len(transport.PACKET_LABEL):]
        if content.startswith('{') and not _matches_retained_bodies(json.loads(content)):
            return False
    return True


def _matches_assignment_bindings(assignment, invocation, observed, route, packet, prompt, history):
    """Bind the packet, rendered prompt and exact retained session history."""
    packet_digest = digest({key: value for key, value in packet.items() if key != 'digest'})
    prompt_hash = digest(prompt)
    if (assignment.get('staged_review') or assignment.get('id') != invocation['assignment_id']
            or assignment.get('route_id') != invocation.get('route_id')
            or assignment.get('route_digest') != digest(route)
            or invocation.get('route_digest') != digest(route)
            or packet.get('digest') != packet_digest
            or assignment.get('prompt', {}).get('rendered_prompt_hash') != prompt_hash
            or not assignment.get('session_id')
            or invocation.get('session_id') != assignment['session_id']
            or invocation.get('invocation_mode') != (
                'explicit-bar-continuation' if history else 'fresh-explicit-input')
            or not _matches_retained_bodies(packet) or not _matches_history_bodies(history)):
        return False
    return all(record.get('packet_digest') == packet_digest and record.get('prompt_hash') == prompt_hash
               for record in (assignment, invocation, observed))


def find_historical_request(records, artifacts, invocation, *, kind):
    """Match whole serialized bytes after ordinary reconstruction found no match.

    Readers and builders deliberately have no exception handler here: an artifact
    failure or a builder exception must remain that failure, never a profile.
    """
    if kind not in ('review_probe_invocation', 'review_invocation'):
        return None
    raw = artifacts.read(invocation['request'])
    recorded = json.loads(raw)
    assignment = None
    if kind == 'review_probe_invocation':
        route = records['review_route'][invocation['route_id']]
    else:
        assignment = records['review_assignment'][invocation['assignment_id']]
        route = records['review_route'][assignment['route_id']]
    observed = _find_failed_observation(records, invocation, kind, route, recorded)
    if observed is None:
        return None
    if assignment is None:
        prompt, packet, history = recorded['instructions'], invocation['positive_controls'], []
        if not _matches_probe_bindings(invocation, observed, prompt, packet):
            return None
    else:
        prompt = assignment['prompt']['text']
        packet = protocol._read_json(artifacts, assignment['packet'])
        history = protocol.read_history(artifacts, assignment)
        if not _matches_assignment_bindings(assignment, invocation, observed, route, packet, prompt, history):
            return None
    for profile, _ in _PROFILES:
        if (profile == 'unlabelled-probe') != (kind == 'review_probe_invocation'):
            continue
        request = build_historical_request(profile, route, prompt, packet, history)
        if request is None:
            continue
        rebuilt = json.dumps(request, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        if rebuilt == raw:
            return dict(sha256=hashlib.sha256(rebuilt).hexdigest(), request_digest=digest(request),
                        historical_profile=profile)
    return None
