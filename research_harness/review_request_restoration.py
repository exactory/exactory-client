"""Versioned give-back of exact current text after the pinned reductions."""

import copy
import json

from . import review_transport as transport
from .errors import ResearchError

PROTOCOL = 'exactory-review-request-restoration-v1'
_RESTORABLE_DELIVERIES = frozenset(('body-elided-route-limit', 'body-elided-previously-supplied'))
# These adapters measure `json.dumps({'review_messages': input}, sort_keys=True, ensure_ascii=False)`, where the
# packet is one JSON string: replacing one evidence entry changes the measure by the difference of the escaped
# serializations of the two entries alone.
_ESCAPED_PACKET_ADAPTERS = frozenset(('codex_cli_v1', 'claude_cli_v1'))


def select_policy(route, packet):
    """Select the prospective text policy without changing other representations."""
    from .review_source_complete import MARKER
    if (route['adapter'] in transport.FITTED_ADAPTERS + ('claude_cli_v1',)
            and transport.local_input_limit(route) is not None and MARKER not in packet
            and 'staged_review' not in packet):
        return PROTOCOL
    return None


def require_policy(route, packet, policy):
    """Reject unsupported metadata instead of silently changing reconstruction."""
    if policy is not None and (policy != PROTOCOL or select_policy(route, packet) != policy):
        raise ResearchError('review_request_policy_mismatch', 'Use the recorded supported request policy')


def _find_restorable(packet, supplied):
    """The omitted current entries whose one exact complete original text the packet holds, in packet order."""
    from .problem_openness_sources import apply_delivery
    originals = copy.deepcopy(packet)
    apply_delivery(originals)
    by_artifact = {}
    for entry in originals.get('evidence', []):
        by_artifact.setdefault(json.dumps(entry.get('artifact'), sort_keys=True), []).append(entry)
    restorable = []
    for index, omitted in enumerate(supplied.get('evidence', [])):
        if (omitted.get('delivery') not in _RESTORABLE_DELIVERIES or omitted.get('content') != ''
                or omitted.get('encoding') != 'utf-8'):
            continue
        candidates = by_artifact.get(json.dumps(omitted.get('artifact'), sort_keys=True), [])
        if (candidates and all(entry == candidates[0] for entry in candidates)
                and transport._complete_text_entry(candidates[0])):
            restorable.append((index, candidates[0]))
    return restorable


def _with_packet(request, supplied):
    """The request with this packet as its current message; every other value is shared with `request`."""
    message = dict(request['input'][-1], content=transport.PACKET_LABEL
                   + json.dumps(supplied, sort_keys=True, ensure_ascii=False))
    return dict(request, input=request['input'][:-1] + [message])


def _measure_escaped(value):
    return len(json.dumps(json.dumps(value, sort_keys=True, ensure_ascii=False), ensure_ascii=False))


def restore_request(route, packet, request, policy):
    """Restore complete current entries in packet order while native input fits.

    Historical messages, source deferrals and HTML extraction substitutions keep
    their exact projection. Only ordinary body omissions are eligible here.
    """
    if policy is None:
        return request
    require_policy(route, packet, policy)
    messages = request.get('input')
    if not isinstance(messages, list) or not messages:
        return request
    current = messages[-1]
    content = current.get('content')
    if (current.get('role') != 'user' or not isinstance(content, str)
            or not content.startswith(transport.PACKET_LABEL)):
        return request
    measure = transport._local_adapter(route).packet_text
    limit = transport.local_input_limit(route)
    size = len(measure(request))
    if size > limit:
        return request
    supplied = json.loads(content[len(transport.PACKET_LABEL):])
    restored = False
    for index, original in _find_restorable(packet, supplied):
        omitted = supplied['evidence'][index]
        if route['adapter'] in _ESCAPED_PACKET_ADAPTERS:
            trial_size = size + _measure_escaped(original) - _measure_escaped(omitted)
        else:
            supplied['evidence'][index] = original
            trial_size = len(measure(_with_packet(request, supplied)))
        if trial_size <= limit:
            supplied['evidence'][index], size, restored = original, trial_size, True
        else:
            supplied['evidence'][index] = omitted
    return copy.deepcopy(_with_packet(request, supplied)) if restored else request
