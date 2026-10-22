"""One explicit source-preserving transition to the native image review route."""

import json

from .errors import ResearchError
from .evidence import digest
from .operations import normalized_text

MARKER = 'visual_route_transition'
PROTOCOL = 'same-result-slot-visual-v1'


def _require(condition, message):
    if not condition:
        raise ResearchError('review_visual_route_mismatch', message)


def validate_transition(records, artifacts, previous_assignment, new_route, new_request_context):
    """Preserve exact scientific identity and the complete authenticated previous exchange."""
    from . import review_protocol, review_transport, review_source_complete, review_context_repair
    prior, context = previous_assignment, new_request_context
    old_route = records.get('review_route', {}).get(prior['route_id'])
    _require(old_route is not None and old_route['adapter'] == 'codex_cli_v1'
             and new_route['adapter'] == 'codex_cli_visual_v1'
             and old_route['endpoint'] == new_route['endpoint'] == 'codex://local'
             and old_route['model'] == new_route['model']
             and prior['route_id'] != new_route['id'], 'Only the same exact model may add native visual input')
    _require(prior['role'] == context['role'] == 'result' and prior.get('subject') == context['subject']
             and context['subject'].get('kind') == 'problem'
             and context['slot_id'] == review_context_repair.slot_id(records, prior)
             and all(normalized_text(prior[key]) == normalized_text(context[key]) for key in ('reviewer_id', 'author_id'))
             and normalized_text(context['reviewer_id']) != normalized_text(context['author_id']),
             'Keep the exact result subject, original reviewer slot and author exclusion')
    previous = json.loads(artifacts.read(prior['packet']))
    packet = context['packet']
    _require(prior.get('context', {}).get('source_delivery') == review_source_complete.MODE
             and review_source_complete.MARKER in previous and review_source_complete.MARKER in packet
             and MARKER not in previous, 'Bridge only the existing complete-source result lineage once')
    _require(all(packet.get(key) == previous.get(key) for key in
                 ('subject', 'bar_ids', 'bars', 'problem_evidence_digest')),
             'The route transition must preserve exact problem evidence and assigned bars')
    from .evaluation import Evaluation
    from .review_visual_sources import MARKER as VISUAL_MARKER, validate_manifest
    visual = packet.get(VISUAL_MARKER)
    _require(isinstance(visual, dict) and bool(visual.get('pages')) and bool(visual.get('readings')),
             'A route transition must supply newly required native PDF page images')
    validate_manifest(Evaluation.of(records, artifacts), visual)
    required = {(row['id'], row.get('digest')) for row in packet.get('problem_evidence', {}).get('readings', [])}
    _require(any((row['id'], row['digest']) in required and row['units'] for row in visual['readings']),
             'At least one exact current required reading inspection must receive its native page')
    # The predecessor is the audited text-only Codex adapter, so these bound PNG pages are a new delivery.
    state = review_protocol.assignment_state(records, artifacts, prior['id'])
    _require(state.get('ready') is True and state.get('attempt_id') is not None and state.get('output') is not None,
             'The preceding result must have a complete authenticated observed output')
    attempt = records.get('review_attempt', {}).get(state['attempt_id'])
    _require(attempt is not None and review_protocol.authenticate_attempt(records, artifacts, prior, attempt),
             'The exact preceding attempt must remain authenticated')
    request = json.loads(artifacts.read(attempt['request']))
    expected = review_transport.logical_messages(request) + [
        {'role': 'assistant', 'content': json.dumps(state['output'], sort_keys=True)}]
    _require(context['history'] == expected, 'Preserve every prior visible message and the exact latest observed output')
    probe = review_protocol.route_state(records, artifacts, new_route['id'])
    _require(probe['context_status'] == 'verified_for_route' and probe['probe_id'] is not None,
             'The exact new image route needs an authenticated native image capability probe')
    return {'protocol': PROTOCOL, 'previous_assignment_id': prior['id'], 'previous_route_id': prior['route_id'],
            'route_id': new_route['id'], 'slot_id': context['slot_id'], 'reviewer_id': prior['reviewer_id'],
            'author_id': prior['author_id'], 'model': new_route['model'], 'probe_id': probe['probe_id'],
            'previous_request_digest': digest(request), 'history_digest': digest(expected)}


def validate_lineage_bridge(records, artifacts, child, parent):
    """Authenticate the exact native bridge stored by the route-changing child."""
    from .evaluation import Evaluation
    artifacts = Evaluation.of(records, artifacts)
    packet = json.loads(artifacts.read(child['packet']))
    # Later probes govern current qualification without rewriting this origin.
    # Keep this filtered view stable only within the caller's immutable snapshot.
    origin = artifacts.once(('review_visual_bridge_origin', child['id'], child['recorded_revision']),
        lambda: Evaluation.of(dict(records, review_route_probe={key: probe
            for key, probe in records.get('review_route_probe', {}).items()
            if probe['recorded_revision'] < child['recorded_revision']}), artifacts))
    expected = validate_transition(origin.records, origin, parent, records['review_route'][child['route_id']],
        {'packet': packet, 'history': child['history'], 'reviewer_id': child['reviewer_id'],
         'author_id': child['author_id'], 'slot_id': child['slot_id'], 'subject': child['subject'], 'role': child['role']})
    _require(packet.get(MARKER) == expected, 'The stored source lineage bridge must equal its validated native transition')
