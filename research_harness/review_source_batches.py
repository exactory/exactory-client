"""Freeze prospective whole source replies against the constructed route input."""

import copy
import json
import os
from pathlib import Path
import tempfile

from . import review_excerpts, review_transport as transport
from .errors import ResearchError
from .evidence import digest

PROTOCOL = 'exactory-review-source-batch-v1'
MARKER = 'source_reply_batch'
# What the reviewer reads in the marker of a batched source continuation.
SOURCE_BATCH_GUIDANCE = (
    'This turn includes the replies in included_request_ids and defers those in deferred_request_ids. A deferred reply '
    'has status deferred_capacity: the reviewer input had no room for it this turn, not that the source is unavailable. '
    'A deferred request stays in pending_source_requests and is answered in a later turn; do not repeat it. While a '
    'request your assessment needs is pending, return an unresolved response with support=null and do not judge '
    'evidence you have not received.')


def digest_request_meaning(request):
    """Digest the exact scientific meaning of a request independently of its turn ID."""
    return digest({key: value for key, value in request.items() if key != 'id'})


def require_progress(previous, packet):
    """Refuse repeated unavailable replies unless request meaning or source state changes."""
    inherited = {digest_request_meaning(row) for row in previous.get('pending_source_requests', [])}
    earlier = {}
    for row in previous.get('source_deliveries', []):
        earlier.setdefault(digest_request_meaning(row['request']), []).append(
            {key: value for key, value in row.items() if key != 'request'})
    deliveries = packet.get('source_deliveries', [])
    if not deliveries or not inherited:
        return
    states = previous.get(MARKER, {}).get('source_states')
    if states is None:
        states = [{'request_meaning': meaning, 'reply_digest': digest(row)}
                  for meaning, rows in earlier.items() for row in rows]
    if any(row not in states for row in packet[MARKER]['source_states']):
        return
    for row in deliveries:
        if row.get('status') in ('deferred_capacity', 'deferred_reading_budget'):
            continue
        meaning = digest_request_meaning(row['request'])
        state = {key: value for key, value in row.items() if key != 'request'}
        if state not in earlier.get(meaning, []):
            return
    raise ResearchError('review_source_no_progress', 'The inherited source requests and bound source state are unchanged',
                        {'pending_source_requests': copy.deepcopy(previous['pending_source_requests']),
                         'source_requests': [copy.deepcopy(row['request']) for row in deliveries]})


def measure_request(route, request):
    """Measure the entire serialization as well as the adapter's actual rendered input."""
    serialized = json.dumps(request, sort_keys=True, ensure_ascii=False, allow_nan=False)
    rendered = serialized
    if route['adapter'] in transport.FITTED_ADAPTERS:
        rendered = transport._local_adapter(route).packet_text(request)
    elif route['adapter'] == 'claude_cli_v1':
        from .review_claude_transport import packet_text
        rendered = packet_text(request)
    elif route['adapter'] == 'codex_cli_visual_v1':
        from .review_codex_visual_transport import packet_text
        rendered = packet_text(request)
        # The native emitter uses this prefix and eight generated characters.
        # Resolve the same directory spelling before counting its image wrappers.
        directory = (Path(os.environ.get('TMPDIR') or tempfile.gettempdir()) /
                     'review-visual-00000000').resolve()
        rendered += ''.join(f'<image name=[Image #{index}] path="{directory / ("image-" + str(index).zfill(3) + ".png")}"></image>'
                            for index, _ in enumerate(request.get('exactory_image_inputs', []), 1))
    # Inline native attachment data, descriptors and wrappers are all in serialized.
    return {'serialized_characters': len(serialized), 'serialized_utf8_bytes': len(serialized.encode('utf-8')),
            'rendered_characters': len(rendered), 'prompt_characters': len(request.get('instructions', ''))}


def project_replies(records, artifacts, packet, included, allowances):
    """Keep whole reply units, shared bodies and exact deferred identities."""
    from .review_protocol import collect_references
    from .review_source_capacity import BODY_DELIVERY
    result = copy.deepcopy(packet)
    result.pop('digest', None)
    result.pop(MARKER, None)
    deliveries = result.get('source_deliveries', [])
    outside = {key: value for key, value in result.items() if key not in ('source_deliveries', 'evidence')}
    retained = {digest(reference) for reference in collect_references(outside)}
    withheld = set()
    pending = {row['id']: row for row in result.get('pending_source_requests', [])}
    passage_requests = [row['request'] for index, row in enumerate(deliveries)
                        if index in included and row['request']['kind'] == 'passage']
    if allowances is not None and review_excerpts.KEY in result:
        answers, after = review_excerpts.answer_passages(records, artifacts, passage_requests, allowances)
        result[review_excerpts.KEY]['allowances'] = after
        answered = {row['request']['id']: row for row in answers}
    else:
        answered = {}
    for index, row in enumerate(deliveries):
        request = row['request']
        if index in included:
            retained.update(digest(reference) for reference in collect_references(row))
            if request['kind'] == 'passage' and request['id'] in answered:
                deliveries[index] = answered[request['id']]
            continue
        withheld.update(digest(reference) for reference in collect_references(row))
        deliveries[index] = {'request': request, 'status': 'deferred_capacity', 'reading_credit': False,
                             'capacity_deferral': {'available_status': row['status'], 'reason': 'reviewer_input_limit'}}
        pending[request['id']] = request
    for entry in result.get('evidence', []):
        identity = digest(entry['artifact'])
        if identity in withheld - retained:
            entry.update(content='', delivery=BODY_DELIVERY)
            if entry.get('encoding') == 'base64':
                entry['encoding'] = 'omitted'
    result['pending_source_requests'] = list(pending.values())
    return result


def is_reply_delivery_complete(artifacts, route, packet, request, included):
    """Check reply metadata and complete required bodies after native request construction."""
    from .review_input_projection import packets_from_request, packets_from_visual_request
    from .review_source_complete import MARKER as COMPLETE_MARKER
    if route['adapter'] == 'codex_cli_visual_v1':
        packets = packets_from_visual_request(request)
    else:
        packets = packets_from_request(request, native_attachments=route['adapter'] == 'openai_responses_v1')
    if not packets:
        return False
    sent = packets[-1]
    actual = sent.get('source_deliveries', [])
    expected = packet.get('source_deliveries', [])
    if len(actual) != len(expected) or sent.get(MARKER) != packet[MARKER]:
        return False
    complete = packet.get(COMPLETE_MARKER, {})
    alternate = set(complete.get('alternate_body_sha256', []))
    from .review_source_capacity import _complete_text
    from .direction_sources import has_attachment
    bodies = {entry['artifact']['sha256']: entry for entry in sent.get('evidence', [])}
    for index in included:
        row, delivered = expected[index], actual[index]
        # The established HTML omission can replace an original with its complete
        # same-capture extraction, but cannot remove reply metadata or that text.
        if any(delivered.get(key) != value for key, value in row.items()
               if key not in ('evidence', 'source_provenance')):
            return False
        omissions = {item['sha256'] for item in delivered.get('transport_omissions', [])
                     if item.get('extraction_sha256') in bodies
                     and _complete_text(bodies[item['extraction_sha256']])}
        expected_references = [reference for reference in row.get('evidence', [])
                               if reference['sha256'] not in alternate]
        if 'evidence' in row and delivered.get('evidence') != expected_references:
            return False
        provenance = copy.deepcopy(row.get('source_provenance', []))
        for source in provenance:
            captured = source.get('delivered_sha256', [])
            if (alternate | omissions).intersection(captured):
                source.setdefault('captured_delivery_sha256', list(captured))
                source['delivered_sha256'] = [identity for identity in captured if identity not in alternate | omissions]
        if 'source_provenance' in row and delivered.get('source_provenance') != provenance:
            return False
        for reference in row.get('evidence', []):
            if reference['sha256'] in alternate | omissions:
                continue
            if not has_attachment(artifacts, sent, reference):
                return False
    return True


def plan_packet(records, artifacts, route, packet, prompt, history):
    """Plan new assignments only; saved packets never pass through this planner."""
    from .review_source_complete import MARKER as COMPLETE_MARKER
    if ('staged_review' in packet or not (packet.get('continuation') or COMPLETE_MARKER in packet)
            or not packet.get('source_deliveries')):
        return packet
    previous, allowances = {}, None
    if packet.get('continuation'):
        from .review_protocol import assignment_state, source_delivery_packet
        parent = records['review_assignment'][packet['continuation']['assignment_id']]
        # The same frozen parent packet that the continuation answered.
        previous = source_delivery_packet(records, artifacts, parent)
        if review_excerpts.KEY in packet:
            # The session allowances before this turn's passage replies; only included replies charge them.
            output = assignment_state(records, artifacts, parent['id'])['output']
            allowances = review_excerpts.carry_session(records, artifacts, previous, output)['allowances']
    limit = transport.local_input_limit(route)
    if route['adapter'] == 'codex_cli_visual_v1':
        from .review_codex_visual_transport import MAX_USER_TEXT
        limit = MAX_USER_TEXT
    count = len(packet['source_deliveries'])

    def build_candidate(included):
        candidate = project_replies(records, artifacts, packet, included, allowances)
        rows = candidate['source_deliveries']
        candidate[MARKER] = {'protocol': PROTOCOL, 'route_digest': digest(route),
            'input_limit_characters': limit, 'input_limit_metric': 'rendered_characters',
            'guidance': SOURCE_BATCH_GUIDANCE,
            'included_request_ids': list(dict.fromkeys(rows[index]['request']['id'] for index in range(count)
                                                      if index in included)),
            'deferred_request_ids': list(dict.fromkeys(rows[index]['request']['id'] for index in range(count)
                                                      if index not in included)),
            'included_requests': [rows[index]['request'] for index in range(count) if index in included],
            'deferred_requests': [rows[index]['request'] for index in range(count) if index not in included],
            'source_states': [{'request_meaning': digest_request_meaning(row['request']),
                               'reply_digest': digest({key: value for key, value in row.items() if key != 'request'})}
                              for row in packet['source_deliveries']],
            'measurement': {}}
        # Measurement includes its own serialized fields. Integer widths stabilize
        # without putting a self-referential request hash into the packet.
        seen = set()
        while True:
            candidate.pop('digest', None)
            candidate['digest'] = digest(candidate)
            request = transport.request_body(route, prompt['text'], candidate, history)
            measured = measure_request(route, request)
            if measured == candidate[MARKER]['measurement']:
                break
            state = tuple(sorted(measured.items()))
            if state in seen:
                # Native history fitting can alternate across a digit boundary.
                # Try a smaller batch; never admit an inconsistent measurement.
                return candidate, request, False, False
            seen.add(state)
            candidate[MARKER]['measurement'] = measured
        # Local adapters bound rendered native user input. Their separately
        # supplied instructions and native files remain in the full measurement.
        fits = limit is None or measured['rendered_characters'] <= limit
        return candidate, request, fits and is_reply_delivery_complete(artifacts, route, candidate, request, included), True

    all_replies = set(range(count))
    candidate, request, fits, stable = build_candidate(all_replies)
    if fits:
        require_progress(previous, review_excerpts.read_sent_packet(request))
        return candidate
    if COMPLETE_MARKER in packet:
        raise ResearchError('review_input_too_large', 'The complete selected source closure exceeds route capacity',
                            {'limiting_component': 'complete_visible_closure', 'route_id': route.get('id'),
                             'source_requests': [row['request'] for row in packet['source_deliveries']],
                             'measurement': measure_request(route, request), 'input_limit_characters': limit,
                             'measurement_stable': stable})
    included = set()
    # Refuse irreducible replies even when another reply happens to fit. Exact
    # fulltext parts supplied by the excerpt protocol are already separate units.
    for index in range(count):
        single, single_request, fits, stable = build_candidate({index})
        if not fits:
            raise ResearchError('review_input_too_large', 'An indivisible source reply cannot fit the reviewer input',
                                {'limiting_component': 'source_reply' if stable else 'source_reply_measurement',
                                 'source_request': copy.deepcopy(packet['source_deliveries'][index]['request']),
                                 'measurement': measure_request(route, single_request),
                                 'input_limit_characters': limit, 'measurement_stable': stable,
                                 'required_representation_complete': is_reply_delivery_complete(
                                     artifacts, route, single, single_request, {index})})
        trial, trial_request, fits, _ = build_candidate(included | {index})
        if fits:
            included.add(index)
            candidate, request = trial, trial_request
    require_progress(previous, review_excerpts.read_sent_packet(request))
    return candidate
