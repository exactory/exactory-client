"""Reconstruct ordinary recorded text requests with exact JSON length trials."""

import json
import math
import re
import weakref

from . import review_transport as transport


def _supports_value(value):
    active, pending = set(), [(value, False)]
    while pending:
        item, leaving = pending.pop()
        if leaving:
            active.remove(id(item))
            continue
        if type(item) in (dict, list):
            if id(item) in active:
                return False
            active.add(id(item))
            pending.append((item, True))
            if type(item) is dict and any(type(key) is not str or re.search('[\ud800-\udfff]', key) for key in item):
                return False
            pending.extend((child, False) for child in (item.values() if type(item) is dict else item))
        elif type(item) is float:
            if not math.isfinite(item):
                return False
        elif type(item) is str:
            if re.search('[\ud800-\udfff]', item):
                return False
        elif type(item) not in (str, int, bool, type(None)):
            return False
    return True


def _copy_value(value, *, measured=True):
    """Copy independent JSON occurrences and annotate their exact character costs."""
    if not isinstance(value, (dict, list)):
        return value
    result = ((_SizedObject() if measured else {}) if isinstance(value, dict)
              else (_SizedArray() if measured else []))
    pending = [(value, result, False)]
    while pending:
        source, target, leaving = pending.pop()
        if leaving:
            if not measured:
                continue
            for escaped in (False, True):
                length = 2 + max(0, len(target) - 1) * 2
                if isinstance(target, dict):
                    length += sum(_measure_string(key, escaped) + 2 + measure_json(item, escaped=escaped)
                                  for key, item in target.items())
                else:
                    length += sum(measure_json(item, escaped=escaped) for item in target)
                target._lengths[escaped] = length
            continue
        pending.append((source, target, True))
        items = source.items() if isinstance(source, dict) else enumerate(source)
        for key, item in items:
            if isinstance(item, dict):
                copied = _SizedObject() if measured else {}
            elif isinstance(item, list):
                copied = _SizedArray() if measured else []
            else:
                copied = item
            if isinstance(target, dict):
                dict.__setitem__(target, key, copied)
            else:
                list.append(target, copied)
            if isinstance(copied, (dict, list)):
                if measured:
                    copied._parent = weakref.ref(target)
                pending.append((item, copied, False))
    return result


def _change_lengths(value, changes):
    while value is not None:
        for index in (0, 1):
            value._lengths[index] += changes[index]
        value = value._parent() if value._parent is not None else None


class _SizedObject(dict):
    """A temporary JSON object whose pinned mutations update ancestor lengths."""

    __slots__ = ('_parent', '_lengths', '__weakref__')

    def __init__(self):
        super().__init__()
        self._parent = None
        self._lengths = [2, 2]

    def __deepcopy__(self, memo):
        return _copy_value(self)

    def __setitem__(self, key, value):
        copied = _copy_value(value)
        changes = [measure_json(copied, escaped=escaped) for escaped in (False, True)]
        if key in self:
            old = self[key]
            for escaped in (False, True):
                changes[escaped] -= measure_json(old, escaped=escaped)
            if isinstance(old, (_SizedObject, _SizedArray)):
                old._parent = None
        else:
            for escaped in (False, True):
                changes[escaped] += _measure_string(key, escaped) + 2 + (2 if self else 0)
        dict.__setitem__(self, key, copied)
        if isinstance(copied, (_SizedObject, _SizedArray)):
            copied._parent = weakref.ref(self)
        _change_lengths(self, changes)

    def __delitem__(self, key):
        old = self[key]
        changes = [-(_measure_string(key, escaped) + 2 + (2 if len(self) > 1 else 0)
                     + measure_json(old, escaped=escaped)) for escaped in (False, True)]
        dict.__delitem__(self, key)
        if isinstance(old, (_SizedObject, _SizedArray)):
            old._parent = None
        _change_lengths(self, changes)

    def update(self, *args, **kwargs):
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def pop(self, key, *default):
        if key not in self:
            return dict.pop(self, key, *default)
        value = self[key]
        del self[key]
        return value

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]


class _SizedArray(list):
    """The indexed replacements and appends used by the pinned reductions."""

    __slots__ = ('_parent', '_lengths', '__weakref__')

    def __init__(self):
        super().__init__()
        self._parent = None
        self._lengths = [2, 2]

    def __deepcopy__(self, memo):
        return _copy_value(self)

    def __setitem__(self, index, value):
        old = self[index]
        copied = _copy_value(value)
        changes = [measure_json(copied, escaped=escaped) - measure_json(old, escaped=escaped)
                   for escaped in (False, True)]
        list.__setitem__(self, index, copied)
        if isinstance(old, (_SizedObject, _SizedArray)):
            old._parent = None
        if isinstance(copied, (_SizedObject, _SizedArray)):
            copied._parent = weakref.ref(self)
        _change_lengths(self, changes)

    def append(self, value):
        copied = _copy_value(value)
        changes = [measure_json(copied, escaped=escaped) + (2 if self else 0) for escaped in (False, True)]
        list.append(self, copied)
        if isinstance(copied, (_SizedObject, _SizedArray)):
            copied._parent = weakref.ref(self)
        _change_lengths(self, changes)


def _measure_string(value, escaped):
    """Count a JSON string, optionally escaped again inside another JSON string."""
    length = len(value) + (4 if escaped else 2)
    length += (3 if escaped else 1) * (value.count('"') + value.count('\\'))
    for match in re.finditer('[\x00-\x1f]', value):
        short = match.group() in '\b\f\n\r\t'
        length += (2 if short else 6) if escaped else (1 if short else 5)
    return length


def measure_json(value, *, escaped=False):
    """Count default-spaced, ensure_ascii=False JSON characters without emitting it."""
    length, pending = 0, [value]
    while pending:
        item = pending.pop()
        if isinstance(item, (_SizedObject, _SizedArray)):
            length += item._lengths[escaped]
        elif type(item) is str:
            length += _measure_string(item, escaped)
        elif type(item) in (dict, list):
            length += 2 + max(0, len(item) - 1) * 2
            if type(item) is dict:
                length += sum(_measure_string(key, escaped) + 2 for key in item)
                pending.extend(item.values())
            else:
                pending.extend(item)
        else:
            length += len(json.dumps(item, ensure_ascii=False))
    return length


def _measure_input(supplied, messages):
    """Include the packet's nested escaping in the adapters' review_messages envelope."""
    message_length = measure_json({'role': 'user', 'content': transport.PACKET_LABEL})
    message_length += measure_json(supplied, escaped=True)
    return (len('{"review_messages": ') + measure_json(messages)
            + (2 if messages else 0) + message_length + 1)


class _MeasuredInput:
    """A source-capacity trial retaining its packet instead of materializing a request."""

    def __init__(self, packet, messages):
        self.packet = packet
        self.length = _measure_input(packet, messages)

    def __len__(self):
        return self.length


def try_build_request(route, prompt, packet, history, order):
    """Return an exact ordinary text request, or None for the pinned builder to handle."""
    from .review_source_complete import MARKER
    if (type(route) is not dict
            or route.get('adapter') not in ('codex_cli_v1', 'claude_cli_v1')
            or type(packet) is not dict or MARKER in packet or 'staged_review' in packet
            or type(history) not in (list, tuple) or type(order) is not tuple
            or order not in (transport.FIT_ORDER,) + transport.EARLIER_FIT_ORDERS
            or not _supports_value(packet) or not _supports_value(route)
            or any(type(message) is not dict or type(message.get('content')) is not str
                   or not _supports_value(message) for message in history)):
        return None
    transport.validate_route(route)
    supplied = _copy_value(packet)
    from .problem_openness_sources import apply_delivery, protected_evidence
    apply_delivery(supplied)
    for evidence in supplied.get('evidence', []):
        if isinstance(evidence, dict) and evidence.get('encoding') == 'base64':
            if 'artifact' not in evidence:
                return None
            evidence.pop('content')
            evidence.update(encoding='omitted', delivery='bytes-omitted-on-text-route', note=transport.OMITTED_BYTES_NOTE)
    fit = transport._Fit(supplied, _copy_value(transport._compact_history(supplied, history)), order)
    limit = transport.local_input_limit(route)
    length = _measure_input(supplied, fit.messages)
    for step in order:
        if length <= limit:
            break
        if step == 'source_capacity':
            from .review_source_capacity import fit as fit_sources
            accepted = fit_sources(supplied, lambda packet: _MeasuredInput(packet, fit.messages),
                                   lambda trial: trial, limit, protected_evidence(supplied))
            if accepted is not None:
                supplied = accepted.packet
                break
            continue
        for _ in transport._REDUCTIONS[step](fit):
            length = _measure_input(supplied, fit.messages)
            if length <= limit:
                break
    return transport._build_local_request(route, prompt, supplied, _copy_value(fit.messages, measured=False))
