"""Describe earlier source bodies absent from a newly fitted session input."""

import copy

from . import review_excerpts, review_parts, review_problem_parts
from . import review_prompt_paragraphs as paragraphs, review_transport as transport
from .evidence import digest
from .evaluation import Evaluation
from .review_delivered_evidence import collect_prior_session_turns, _read_authenticated_delivery
from .review_input_projection import packets_from_request, packets_from_visual_request


def _read_fitted_request(records, artifacts, route, packet, prompt, history):
    from .review_request_evaluation import request_for_order
    from .review_request_restoration import select_policy
    evaluation = Evaluation.of(records, artifacts)
    request = request_for_order(evaluation, route, prompt['text'], packet, history, transport.FIT_ORDER,
                                policy=select_policy(route, packet))
    packets = (packets_from_visual_request(request) if route['adapter'] == 'codex_cli_visual_v1' else
               packets_from_request(request, native_attachments=route['adapter'] == 'openai_responses_v1'))
    return request, packets


def _collect_source_bodies(records, artifacts, assignment):
    """Keep exact delivered bodies with their version, original digest and turn."""
    evaluation = Evaluation.of(records, artifacts)
    bodies = []
    for parent, attempt in collect_prior_session_turns(records, evaluation, assignment):
        view = _read_authenticated_delivery(records, evaluation, parent, attempt)['current_input']
        packet = view['packets'][-1]
        complete = review_excerpts.find_complete_bodies(packet)
        entries = {entry['artifact']['sha256']: entry for entry in packet.get('evidence', [])
                   if entry['artifact']['sha256'] in complete}
        for delivery in packet.get('source_deliveries', []):
            request = delivery.get('request', {})
            if request.get('kind') != 'source' or delivery.get('status') not in ('delivered', 'partial'):
                continue
            for reference in delivery.get('evidence', []):
                entry = entries.get(reference['sha256'])
                if entry is None:
                    continue
                original = delivery.get('origin', {}).get('artifact_descriptor', reference)
                locator = delivery.get('origin', {}).get('locator', {})
                start = locator.get('start', 0)
                bodies.append(dict(version_id=request['version_id'], depth=request['depth'],
                    text_sha256=original['sha256'], artifact=reference,
                    span=[start, locator.get('end', start + len(entry['content']))],
                    turn=dict(assignment_id=parent['id'], attempt_id=attempt['id'])))
    return bodies


def _describe_missing_sources(bodies, packets, packet):
    from .review_staged import interval_gaps
    complete = set().union(*(review_excerpts.find_complete_bodies(item) for item in packets))
    grouped = {}
    excerpts = packet.get(review_excerpts.KEY, {}).get('excerpts', [])
    for body in bodies:
        key = (body['version_id'], body['depth'], body['text_sha256'])
        grouped.setdefault(key, []).append(body)
    rows = []
    for (_, _, original), deliveries in grouped.items():
        if original in complete:
            continue
        held = [body['span'] for body in deliveries if body['artifact']['sha256'] in complete]
        missing = interval_gaps([body['span'] for body in deliveries], held)
        if not missing:
            continue
        body = deliveries[0]
        row = {name: copy.deepcopy(body[name]) for name in ('version_id', 'depth', 'text_sha256')}
        turns, credited = {}, list(held)
        for delivery in deliveries:
            contribution = interval_gaps([delivery['span']], credited)
            if contribution:
                turn = delivery['turn']
                key = (turn['assignment_id'], turn['attempt_id'])
                turns.setdefault(key, dict(turn, characters=0))['characters'] += sum(
                    end - start for start, end in contribution)
                credited.extend(contribution)
        row['turns'] = list(turns.values())
        row.update(characters=sum(end - start for start, end in missing), verified_excerpts=sum(
            excerpt.get('status') == 'verified' and excerpt.get('version_id') == body['version_id']
            and excerpt.get('artifact_descriptor', {}).get('sha256') == original for excerpt in excerpts))
        rows.append(row)
    return rows


def _append_notice_prompt(prompt, role):
    base = paragraphs.strip_prompt_paragraphs(prompt, role)
    parameters = prompt['parameters']
    versions = list(parameters.get(paragraphs.PARAMETER, [])) + [paragraphs.DELIVERY_NOTICE_PROTOCOL]
    return paragraphs.append_prompt_paragraphs(base, role, parameters.get(paragraphs.CARRY_PARAMETER), versions)


def prepare_notice(records, artifacts, route, assignment, packet, prompt, history):
    """Fit a new continuation, reserving notice rows before reducing its history.

    Stored assignments never run this planner. Start with room for every earlier
    source; then discard rows for bodies that the actual fitted input still holds.
    Freeze the admitted replies and the fitted history before shrinking the
    notice; a smaller row set cannot reselect a different batch of replies.
    """
    from .review_source_batches import MARKER, measure_request, plan_packet
    from .review_source_complete import MARKER as COMPLETE_MARKER
    planned = plan_packet(records, artifacts, route, packet, prompt, history)
    if (not packet.get('continuation') or packet.get(review_parts.KEY)
            or packet.get(review_problem_parts.KEY)):
        return planned, prompt, history
    bodies = _collect_source_bodies(records, artifacts, assignment)
    if not bodies:
        return planned, prompt, history
    _, actual = _read_fitted_request(records, artifacts, route, planned, prompt, history)
    if not _describe_missing_sources(bodies, actual, actual[-1]):
        return planned, prompt, history
    notice_prompt = _append_notice_prompt(prompt, assignment['role'])
    rows = _describe_missing_sources(bodies, [], planned)
    candidate = copy.deepcopy(packet)
    candidate[paragraphs.DELIVERY_NOTICE_KEY] = dict(protocol=paragraphs.DELIVERY_NOTICE_PROTOCOL, sources=rows)
    candidate.pop('digest', None)
    candidate['digest'] = digest(candidate)
    candidate = plan_packet(records, artifacts, route, candidate, notice_prompt, history)
    selected_prompt = notice_prompt
    complete = COMPLETE_MARKER in candidate
    while True:
        request, actual = _read_fitted_request(records, artifacts, route, candidate, selected_prompt, history)
        missing = _describe_missing_sources(bodies, actual, actual[-1])
        measurement = measure_request(route, request) if MARKER in candidate and not complete else None
        if (missing == rows and (measurement is None or candidate[MARKER]['measurement'] == measurement)):
            return candidate, selected_prompt, history
        # Retain the reply set chosen with every row reserved. Fitting the
        # smaller metadata can only reduce this already fitted history further.
        # Freeze the current projection too: restoration must not alternate
        # across a measurement field's integer-width boundary.
        # Complete-visible inputs bind their original history and derive a
        # fixed projection from it. Their logical inputs must stay intact.
        if not complete:
            candidate = copy.deepcopy(actual[-1])
            history = transport.logical_messages(request)[:-1]
        rows = missing
        if rows:
            candidate[paragraphs.DELIVERY_NOTICE_KEY] = dict(protocol=paragraphs.DELIVERY_NOTICE_PROTOCOL, sources=rows)
        else:
            candidate.pop(paragraphs.DELIVERY_NOTICE_KEY, None)
            selected_prompt = prompt
        if measurement is not None:
            candidate[MARKER]['measurement'] = measurement
        candidate.pop('digest', None)
        candidate['digest'] = digest(candidate)
        if complete:
            # Its body set is fixed, but v1/v2 encoding can change at the cap.
            # Keep the existing planner's measurement stabilization and refusal.
            return plan_packet(records, artifacts, route, candidate, selected_prompt, history), selected_prompt, history
