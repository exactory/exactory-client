"""Reuse exact request derivations without changing the pinned transport."""

import hashlib
import json
import zlib

from . import review_transport as transport


def _encode_exact(value):
    """Encode only JSON-native finite values, preserving types and object order."""
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        pending = [value]
        while pending:
            item = pending.pop()
            if type(item) is dict:
                if any(type(key) is not str for key in item):
                    return None
                pending.extend(item.values())
            elif type(item) is list:
                pending.extend(item)
            elif type(item) not in (str, int, float, bool, type(None)):
                return None
        return encoded
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        # Encoding is optional. The transport's native result and refusals stand.
        return None


def _build_request(route, prompt, packet, history, order):
    """Fit supported recorded text; pass every other representation to the pinned builder."""
    from .review_request_fit import try_build_request
    request = try_build_request(route, prompt, packet, history, order)
    if request is not None:
        return request
    if order is transport.FIT_ORDER:
        return transport.request_body(route, prompt, packet, history)
    return transport.request_body(route, prompt, packet, history, order)


def request_for_order(evaluation, route, prompt, packet, history, order, *, policy=None):
    """Derive once for the complete actual inputs; return an independent value."""
    if (type(history) not in (list, tuple) or type(order) is not tuple
            or any(type(step) is not str for step in order)):
        from .review_request_restoration import restore_request
        return restore_request(route, packet, _build_request(route, prompt, packet, history, order), policy)
    encoded_inputs = [_encode_exact(value) for value in (route, prompt, packet, list(history), policy)]
    if any(value is None for value in encoded_inputs):
        from .review_request_restoration import restore_request
        return restore_request(route, packet, _build_request(route, prompt, packet, history, order), policy)
    key = (tuple(hashlib.sha256(value).digest() for value in encoded_inputs),
           tuple(order), transport.local_input_limit(route))
    uncached = []

    def derive():
        from .review_request_restoration import restore_request
        request = _build_request(route, prompt, packet, history, order)
        request = restore_request(route, packet, request, policy)
        encoded = _encode_exact(request)
        if encoded is None:
            uncached.append(request)
            return None
        return zlib.compress(encoded, 6)

    compressed = evaluation.request_once(key, derive)
    return uncached[0] if compressed is None else json.loads(zlib.decompress(compressed).decode('utf-8'))


def iter_built_requests(evaluation, route, prompt, packet, history=(), *, policy=None):
    """Yield the pinned iterator's candidates lazily, with the same equality rule."""
    from .review_request_restoration import require_policy
    require_policy(route, packet, policy)
    if route['adapter'] == 'codex_cli_visual_v1':
        yield from transport.list_built_requests(route, prompt, packet, history)
        return
    current = request_for_order(evaluation, route, prompt, packet, history, transport.FIT_ORDER, policy=policy)
    yield current
    if route['adapter'] not in transport.FITTED_ADAPTERS + ('claude_cli_v1',):
        return
    seen = [current]
    for order in transport.EARLIER_FIT_ORDERS:
        built = request_for_order(evaluation, route, prompt, packet, history, order, policy=policy)
        if built not in seen:
            seen.append(built)
            yield built
