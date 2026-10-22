"""Deterministic capacity deferrals of whole requested source groups."""

import copy
import hashlib

PROTOCOL = 'exactory-review-source-capacity-v1'
MARKER = 'source_capacity_delivery'
BODY_DELIVERY = 'body-deferred-source-capacity'
NOTE = (
    'These exact source bytes are locally available but deferred by the reviewer route input limit. '
    'Capacity deferral is not source unavailability, delivery, inspection, or reading credit. '
    'The exact requests remain pending in this same review session even if the response omits them. '
    'Inspect the supplied complete source groups and return an unresolved phase response with support=null. '
    'A final scientific assessment cannot be recorded until all pending requests are supplied.')


def _references(value):
    """Conservatively retain any named artifact outside the deferred source groups."""
    found = set()
    if isinstance(value, dict):
        if isinstance(value.get('sha256'), str):
            found.add(value['sha256'])
        for item in value.values():
            found.update(_references(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_references(item))
    return found


def _complete_text(entry):
    content = entry.get('content')
    reference = entry.get('artifact') or {}
    if entry.get('encoding') != 'utf-8' or not isinstance(content, str) or not content:
        return False
    data = content.encode('utf-8')
    return len(data) == reference.get('size') and hashlib.sha256(data).hexdigest() == reference.get('sha256')


def _projection(supplied, candidates, kept, protected, limit):
    retain = set(protected)
    for index, delivery in enumerate(supplied['source_deliveries']):
        if index not in candidates or index in kept:
            retain.update(reference['sha256'] for reference in delivery.get('evidence', []))
    withheld = {reference['sha256'] for index in candidates if index not in kept
                for reference in supplied['source_deliveries'][index]['evidence']} - retain
    withheld &= {entry['artifact']['sha256'] for entry in supplied.get('evidence', []) if _complete_text(entry)}
    if not withheld:
        return None
    result = copy.deepcopy(supplied)
    for entry in result.get('evidence', []):
        if entry['artifact']['sha256'] in withheld:
            entry.update(content='', delivery=BODY_DELIVERY, note=NOTE)
    pending = {request['id']: request for request in result.get('pending_source_requests', [])}
    deferred = []
    for index in candidates:
        delivery = result['source_deliveries'][index]
        missing = sorted(reference['sha256'] for reference in delivery['evidence'] if reference['sha256'] in withheld)
        if not missing:
            continue
        request = delivery['request']
        deferred.append(request['id'])
        pending[request['id']] = request
        delivery['capacity_deferral'] = {'available_status': delivery['status'], 'withheld_sha256': missing,
                                         'reason': 'reviewer_input_limit', 'note': NOTE}
        delivery.update(status='deferred_capacity', reason=NOTE, reading_credit=False)
        delivery['evidence'] = [reference for reference in delivery['evidence'] if reference['sha256'] not in withheld]
        for provenance in delivery.get('source_provenance', []):
            captured = provenance.get('delivered_sha256', [])
            provenance.setdefault('captured_delivery_sha256', list(captured))
            provenance['delivered_sha256'] = [sha for sha in captured if sha not in withheld]
    result['pending_source_requests'] = list(pending.values())
    result[MARKER] = {'protocol': PROTOCOL, 'packet_digest': supplied.get('digest'),
                      'input_limit_characters': limit, 'deferred_request_ids': deferred, 'note': NOTE}
    return result


def fit(supplied, build_request, measure, limit, protected=()):
    """Return a fitting whole-source batch, or None when no complete group can fit.

    The caller uses this only after existing fitting rules fail. No source is
    truncated, and artifacts shared with retained groups or other evidence stay.
    """
    if not isinstance(supplied.get('continuation'), dict):
        return None
    candidates = [index for index, delivery in enumerate(supplied.get('source_deliveries', []))
                  if delivery.get('status') == 'delivered' and delivery.get('evidence')
                  and delivery.get('request', {}).get('kind') == 'source'
                  and delivery['request'].get('depth') == 'fulltext']
    if len(candidates) < 2:
        return None
    outside = {key: value for key, value in supplied.items() if key not in ('evidence', 'source_deliveries')}
    protected = set(protected) | _references(outside)
    kept, accepted = set(), None
    for index in candidates:
        trial = kept | {index}
        projected = _projection(supplied, candidates, trial, protected, limit)
        if projected is None:
            continue
        request = build_request(projected)
        if len(measure(request)) <= limit:
            kept, accepted = trial, request
    return accepted
