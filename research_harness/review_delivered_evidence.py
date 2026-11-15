"""Derive separate delivery views from exact authenticated review invocations."""

import copy
import json

from .errors import ResearchError
from .evaluation import Evaluation
from . import review_protocol as protocol, review_problem_parts as parts, review_parts as fresh
from .review_input_projection import packets_from_request, packets_from_visual_request


def project_delivery_view(records, artifacts, packets):
    """Index only carried records, exact bodies and mappings whose excerpts arrived.

    Consumers resolve carried_records with find_carried_record(..., frozen=True),
    preserving its existing digest-less and conflicting-version rules. Packet
    locators and rendered-page provenance retain their existing bounded checks.
    """
    from .direction_sources import has_attachment
    from .review_objection_evidence import resolved_objection_entries
    from .review_supplied_evidence import (list_delivered_objections, list_delivered_parts,
                                           register_supplied_artifact)
    evaluation = Evaluation.of(records, artifacts)
    packets = copy.deepcopy(packets)
    references, mappings = {}, []
    # Reconstruct only fully covered exact originals from authenticated segment packets.
    segments = {}
    for packet in packets:
        for entry in packet.get('evidence', []):
            if 'segment' in entry and 'original_artifact' in entry:
                original = entry['original_artifact']
                segments.setdefault(original['sha256'], original)
    for original in segments.values():
        if fresh.has_series_attachment(evaluation, packets, original):
            register_supplied_artifact(references, original)
    for packet in packets:
        for entry in packet.get('evidence', []):
            reference = entry['artifact']
            if (entry.get('encoding') in ('utf-8', 'base64') and isinstance(entry.get('content'), str)
                    and has_attachment(evaluation, packet, reference)):
                register_supplied_artifact(references, reference)
    for packet in packets:
        candidates = (packet.get('prior_context', []) + packet.get('version_source_mappings', [])
                      + packet.get('supplied_source_mappings', []) + list_delivered_parts(packet))
        candidates += [mapping for objection in list_delivered_objections(packet)
                       for mapping in resolved_objection_entries(packet, objection, 'version_source_mappings')]
        for mapping in candidates:
            if ('origin' in mapping and mapping.get('excerpt') in references.values()
                    and mapping not in mappings):
                mappings.append(mapping)
    carried = protocol.collect_carried_records(records, evaluation, None, packets=packets)
    return dict(packets=packets, artifacts=references, source_mappings=mappings, carried_records=carried)


def resolve_part_source_link(link, packets):
    """Check reading prerequisites on the whole-text origin of an exact delivered part citation.

    The review keeps its part-relative locator. Only the prerequisite lookup uses
    the bound full-text artifact and the corresponding offsets in that text.
    """
    from .review_supplied_evidence import list_delivered_parts
    from .review_refusal_notice import PROTOCOL as REFUSAL_NOTICE_PROTOCOL
    if not any(packet.get('output_refusal', {}).get('protocol') == REFUSAL_NOTICE_PROTOCOL for packet in packets):
        return link
    locator = link.get('locator', {})
    if locator.get('kind') != 'span':
        return link
    start, end = locator['start'], locator['end']
    for packet in packets:
        for delivery in list_delivered_parts(packet):
            part, origin = delivery['part'], delivery['origin']
            if (delivery['version_id'] == link['version_id'] and delivery['excerpt'] == link['artifact']
                    and origin['source_id'] == link['source_id']
                    and 0 <= start < end <= part['end'] - part['start']):
                descriptor = origin['artifact_descriptor']
                artifact = dict(path=descriptor['location'], **{key: descriptor[key]
                    for key in ('sha256', 'size', 'media_type')})
                return dict(link, artifact=artifact,
                            locator=dict(locator, start=part['start'] + start, end=part['start'] + end))
    return link


def validate_review_readings(records, artifacts, assignment, attempt, references, *, review_binding=False):
    """Keep scientific citations unchanged while checking the reading of each part's origin."""
    from .strategy import evidence
    from .review_context_repair import _exposure_lineage
    if not any('output_refusal' in row.get('context', {}) for row in _exposure_lineage(records, assignment)):
        return evidence(records, artifacts, references, review_binding=review_binding)
    packets = read_chain_delivery(records, artifacts, assignment, attempt=attempt)['packets']
    mapped = [dict(reference, link=resolve_part_source_link(reference['link'], packets))
              if reference.get('kind') == 'source' else reference for reference in references]
    evidence(records, artifacts, mapped, review_binding=review_binding)


def read_attempt_delivery(records, artifacts, assignment, attempt=None):
    """Authenticate an explicit input; ordinary defaults retain the selected attempt.

    Recorded v1 series keep their first-authenticated-input contract. Citation
    delivery does not require current scientific readiness. Each view contains
    packets, artifacts, source_mappings and carried_records.
    current_input is exactly the named request. citation_history additionally
    admits only the legacy part contract's historical deliveries. Coverage uses
    the current part series alone. Authentication or decoding failure raises an
    existing review error and supplies no saved-packet fallback.
    """
    evaluation = Evaluation.of(records, artifacts)
    if attempt is None:
        if records.get('review_assignment', {}).get(assignment.get('id')) != assignment:
            raise ResearchError('review_invocation_unverified', 'Select the exact recorded assignment')
        evidence_parts = assignment.get('prompt', {}).get('parameters', {}).get('evidence_parts')
        if isinstance(evidence_parts, str) and evidence_parts.startswith(parts.PROTOCOL + '/'):
            # The recorded v1 contract reads its first authenticated input even
            # when its output is refused or its current lineage is contaminated.
            candidates = sorted((row for row in records.get('review_attempt', {}).values()
                                 if row['assignment_id'] == assignment['id'] and row.get('status') == 'completed'),
                                key=lambda row: row['recorded_revision'])
            for candidate in candidates:
                if protocol.authenticate_attempt(records, evaluation, assignment, candidate):
                    return _read_authenticated_delivery(records, evaluation, assignment, candidate)
            raise ResearchError('review_invocation_unverified', 'Read the authenticated historical series input')
        state = protocol.assignment_state(records, evaluation, assignment['id'])
        attempt = records.get('review_attempt', {}).get(state.get('attempt_id'))
        if attempt is None:
            raise ResearchError('review_invocation_unverified', 'Citation history requires its selected attempt')
    if (records.get('review_assignment', {}).get(assignment.get('id')) != assignment
            or records.get('review_attempt', {}).get(attempt.get('id')) != attempt
            or attempt.get('assignment_id') != assignment.get('id')
            or attempt.get('status') != 'completed'
            or not protocol.authenticate_attempt(records, evaluation, assignment, attempt)):
        raise ResearchError('review_invocation_unverified', 'Delivery requires the exact authenticated completed attempt')
    return _read_authenticated_delivery(records, evaluation, assignment, attempt)


def collect_exhaustion_predecessor_turns(records, artifacts, assignment):
    """Credit exact earlier inputs only across an exhaustion repair of the same identity and route."""
    from . import review_context_repair as repair

    repairs = [row for row in repair._lineage(records, assignment)
               if row.get('context', {}).get('context_repair') is not None]
    if not repairs:
        return []
    bound = repairs[-1]
    binding = bound['context']['context_repair']
    if binding.get('exhaustion', {}).get('protocol') != repair.EXHAUSTION_PROTOCOL:
        return []
    if records.get('review_assignment', {}).get(bound['id']) != bound:
        return []
    predecessor = records['review_assignment'][binding['assignment_id']]
    if (bound['route_id'] != predecessor['route_id']
            or protocol.normalized_text(bound['reviewer_id']) != protocol.normalized_text(predecessor['reviewer_id'])):
        return []
    evaluation = Evaluation.of(records, artifacts)
    evaluation.once(('exhaustion_delivery_origin', bound['id']),
                    lambda: repair.validate_repair_origin(records, evaluation, bound))
    exposure = repair._exposure_lineage(records, predecessor)
    context_roots = [row for row in exposure if row.get('context', {}).get('context_repair')
                     and 'output_refusal' not in row.get('context', {})]
    context_start = max((row['recorded_revision'] for row in context_roots),
                        default=exposure[0]['recorded_revision'])
    if any(context_start < event['recorded_revision'] < bound['recorded_revision']
           for event in repair.cause_events(records, {row['id'] for row in exposure})):
        return []
    ancestors, pending, seen = [], [predecessor['id']], set()
    while pending:
        identifier = pending.pop()
        if identifier in seen:
            continue
        seen.add(identifier)
        prior = records['review_assignment'][identifier]
        if (prior['role'] != bound['role'] or prior['route_id'] != bound['route_id']
                or protocol.normalized_text(prior['reviewer_id']) != protocol.normalized_text(bound['reviewer_id'])
                or repair.slot_id(records, prior) != repair.slot_id(records, bound)):
            continue
        ancestors.append(prior)
        context = prior.get('context', {})
        parents = [prior.get('history_assignment_id'), context.get('prior_assignment_id'),
                   context.get('output_refusal', {}).get('assignment_id')]
        pending.extend(parent for parent in parents if parent is not None)
    turns = []
    for prior in sorted(ancestors, key=lambda row: (row['recorded_revision'], row['id'])):
        attempts = sorted((row for row in records.get('review_attempt', {}).values()
                           if row['assignment_id'] == prior['id'] and row['status'] == 'completed'),
                          key=lambda row: (row['recorded_revision'], row['id']))
        for attempt in attempts:
            if protocol.authenticate_attempt(records, evaluation, prior, attempt):
                turns.append((prior, attempt))
    return turns


def collect_prior_session_turns(records, artifacts, assignment):
    """Select verified earlier turns of this session, oldest first.

    Exhaustion repairs additionally retain the authenticated inputs of their
    predecessor when both reviewer and route identities remain the same.
    """
    from .bar_sources import ROLES
    if (assignment.get('role') not in ROLES or assignment.get(fresh.KEY)
            or assignment.get('staged_review')
            or assignment.get('prompt', {}).get('parameters', {}).get('evidence_parts')):
        return []
    turns, seen = [], {assignment['id']}
    identifier = assignment.get('history_assignment_id')
    while identifier is not None and identifier not in seen:
        seen.add(identifier)
        parent = records.get('review_assignment', {}).get(identifier)
        if (parent is None or parent.get('session_id') != assignment.get('session_id')
                or protocol.normalized_text(parent['reviewer_id']) != protocol.normalized_text(assignment['reviewer_id'])):
            break
        state = protocol.assignment_state(records, artifacts, identifier)
        attempt = records.get('review_attempt', {}).get(state.get('attempt_id'))
        if (state['ready'] and attempt is not None and attempt.get('status') == 'completed'
                and protocol.authenticate_attempt(records, artifacts, parent, attempt)):
            turns.append((parent, attempt))
        identifier = parent.get('history_assignment_id')
    earlier = collect_exhaustion_predecessor_turns(records, artifacts, assignment)
    seen_attempts = {attempt['id'] for _, attempt in earlier}
    return earlier + [(parent, attempt) for parent, attempt in reversed(turns)
                      if attempt['id'] not in seen_attempts]


def _read_session_delivery(records, artifacts, assignment, delivery):
    """Add only the current packet of each counted turn, never its saved packet."""
    packets = []
    for parent, attempt in collect_prior_session_turns(records, artifacts, assignment):
        observed = _read_authenticated_delivery(records, artifacts, parent, attempt)
        packets.append(observed['current_input']['packets'][-1])
    if not packets:
        return delivery
    packets += delivery['packets']
    unique = []
    for packet in packets:
        if packet not in unique:
            unique.append(packet)
    return fresh.read_series_delivery(records, artifacts, packets=unique)


def read_chain_delivery(records, artifacts, assignment, *, attempt=None):
    """Read citation delivery from authenticated actual turns of one chain."""
    evaluation = Evaluation.of(records, artifacts)
    if assignment.get('staged_review', {}).get('kind') == 'controller':
        return read_selected_delivery(records, evaluation, assignment['id'], require_ready=False)['citation_history']
    delivery = read_attempt_delivery(records, evaluation, assignment, attempt)['citation_history']
    delivery = _read_session_delivery(records, evaluation, assignment, delivery)
    return _read_replaced_delivery(records, evaluation, assignment, delivery)


def _read_replaced_delivery(records, artifacts, assignment, delivery):
    """Credit the actual inputs of refused turns, independently of their output readiness."""
    packets, pending, seen = [], [assignment], set()
    while pending:
        current = pending.pop()
        if current['id'] in seen:
            continue
        seen.add(current['id'])
        parents = (current.get('history_assignment_id'), current.get('context', {}).get('prior_assignment_id'))
        pending.extend(records['review_assignment'][identifier] for identifier in parents
                       if identifier in records.get('review_assignment', {}))
        if 'output_refusal' not in current.get('context', {}):
            continue
        frozen = protocol._read_json(artifacts, current['packet'])
        for entry in frozen['output_refusal']['refused']:
            prior = records['review_assignment'][entry['assignment_id']]
            attempt = records['review_attempt'][entry['attempt_id']]
            observed = read_attempt_delivery(records, artifacts, prior, attempt)['citation_history']
            observed = _read_session_delivery(records, artifacts, prior, observed)
            packets.extend(observed['packets'])
    if not packets:
        return delivery
    unique = []
    for packet in packets + delivery['packets']:
        if packet not in unique:
            unique.append(packet)
    return fresh.read_series_delivery(records, artifacts, packets=unique)


def _read_authenticated_delivery(records, evaluation, assignment, attempt):
    """Project a delivery after explicit authentication or ready-state selection."""
    for name in ('request', 'response', 'output'):
        reference = attempt.get(name)
        if reference is not None:
            evaluation.read(reference)

    def project():
        evidence_parts = assignment.get('prompt', {}).get('parameters', {}).get('evidence_parts')
        legacy_series = isinstance(evidence_parts, str) and evidence_parts.startswith(parts.PROTOCOL + '/')
        try:
            raw = protocol.read_raw_observation(records, evaluation, attempt['id'])
            if raw['request_digest'] != attempt.get('request_digest'):
                raise ResearchError('review_invocation_unverified', 'Delivery requires the exact recorded request digest')
            request = protocol._read_json(evaluation, raw['request'])
            route = records['review_route'][assignment['route_id']]
            ancestors, pending, seen = [], [assignment['id']], set()
            while pending:
                identifier = pending.pop()
                if identifier in seen:
                    continue
                seen.add(identifier)
                ancestor = records.get('review_assignment', {}).get(identifier)
                if ancestor is None:
                    if legacy_series:
                        continue
                    raise ResearchError('review_input_unreadable', 'The exact bound input ancestry is missing')
                ancestors.append(ancestor)
                from .review_supplied_evidence import find_scientific_predecessor
                parents = [ancestor.get('history_assignment_id'), ancestor.get('context', {}).get('prior_assignment_id'),
                           find_scientific_predecessor(ancestor)]
                if not any(parents) and ancestor.get('role') == 'slate' and ancestor.get('session_id') != identifier:
                    parents.append(ancestor.get('session_id'))
                pending.extend(parent for parent in parents if parent is not None)
            binding = assignment.get('staged_review') or {}
            if binding.get('kind') == 'task':
                from .review_excerpts import read_sent_packet
                from .review_input_projection import collect_attachments, project_evidence
                from .review_transport import logical_messages
                packet = read_sent_packet(request)
                if (packet is None or packet.get('digest') != assignment['packet_digest']
                        or packet.get('staged_review', {}).get('epoch_id') != binding['epoch_id']
                        or packet['staged_review'].get('task', {}).get('id') != binding['task_id']
                        or packet.get('role') != assignment['role'] or packet.get('subject') != assignment.get('subject')):
                    raise ResearchError('review_input_unreadable', 'Bind the exact staged task input')
                content = logical_messages(request)[-1]['content']
                project_evidence(packet, collect_attachments(content) if isinstance(content, list) else {})
                packets = [packet]
            else:
                packets = (packets_from_visual_request(request, assignments=ancestors)
                           if route['adapter'] == 'codex_cli_visual_v1' else
                           packets_from_request(request, native_attachments=route['adapter'] == 'openai_responses_v1',
                                                assignments=None if legacy_series else ancestors))
                if not packets or packets[-1]['digest'] != assignment['packet_digest']:
                    raise ResearchError('review_input_unreadable', 'The deciding request must carry its exact current packet')
            current = project_delivery_view(records, evaluation, packets)
            citation, coverage = list(packets), list(packets)
            if parts.find_stage(packets[-1]) is not None:
                # Never select this attempt again while its output is being classified.
                prior = assignment.get('history_assignment_id')
                earlier = parts.collect_series_packets(records, evaluation, prior) if prior else []
                coverage = parts.verified_coverage_packets(records, evaluation, assignment['id'], packets + earlier)
                citation += earlier + parts.collect_earlier_series_packets(records, evaluation, assignment)
            if fresh.find_kind(packets[-1]) is not None:
                prior = assignment.get('context', {}).get('prior_assignment_id')
                earlier = fresh.collect_series_packets(records, evaluation, prior) if prior else []
                series_id = packets[-1][fresh.KEY]['series_assignment_id']
                earlier = [packet for packet in earlier if packet[fresh.KEY]['series_assignment_id'] == series_id]
                coverage = earlier + packets
                citation = earlier + packets
            identities = []
            for packet in packets:
                owner = next((owner for owner in ancestors if owner['packet_digest'] == packet['digest']), None)
                if owner is None and not legacy_series:
                    raise ResearchError('review_input_unreadable', 'Bind each actual packet to its input ancestry')
                owner = owner or {}
                identities.append(dict(assignment_id=owner.get('id'),
                    **{key: copy.deepcopy(packet.get(key, owner.get(key))) for key in
                       ('digest', 'protocol', 'role', 'reviewer_id', 'dossier_id', 'subject')}))
            dependencies = [assignment['packet']]
            series_digests = {packet['digest'] for packet in citation + coverage
                              if parts.find_stage(packet) is not None or fresh.find_kind(packet) is not None}
            for ancestor in ancestors:
                if ancestor['id'] == assignment['id'] or ancestor.get('packet_digest') not in series_digests:
                    continue
                dependencies.append(ancestor['packet'])
                state = protocol.assignment_state(records, evaluation, ancestor['id'])
                selected = records.get('review_attempt', {}).get(state.get('attempt_id'), {})
                dependencies.extend(selected[name] for name in ('request', 'response', 'output')
                                    if selected.get(name) is not None)
            result = dict(assignment_id=assignment['id'], attempt_id=attempt['id'], request_digest=raw['request_digest'],
                        packet_identities=identities, current_input=current,
                        citation_history=fresh.read_series_delivery(records, evaluation, packets=citation),
                        coverage_witnesses=fresh.read_series_delivery(records, evaluation, packets=coverage))
            return result, dependencies
        except ResearchError:
            raise
        except json.JSONDecodeError as error:
            raise ResearchError('review_input_unreadable', 'The exact authenticated input cannot be decoded') from error

    scope = evaluation.find_root()._runtime_scope[0]
    if scope is None or scope['fingerprint'] is None or assignment.get('staged_review'):
        result, dependencies = project()
    else:
        key = ('authenticated_delivery', assignment['id'], attempt['id'], scope['fingerprint'])
        result, dependencies = evaluation.reuse_value(key, project)
    for reference in dependencies:
        evaluation.read(reference)
    return result


def read_selected_delivery(records, artifacts, assignment_id, *, require_ready=True):
    """Select at a gate boundary, retaining a metadata correction's original attempt.

    A staged controller has no scientific invocation of its own. Its accepted
    task graph supplies coverage and controller citations; explicit task reads
    above remain confined to that task's input, including its bound dependencies.
    """
    evaluation = Evaluation.of(records, artifacts)
    assignment = records.get('review_assignment', {}).get(assignment_id)
    controller = (assignment or {}).get('staged_review', {}).get('kind') == 'controller'
    state = (protocol.assignment_state(records, evaluation, assignment_id)
             if require_ready or not controller else {})
    if assignment is None or require_ready and not state['ready']:
        raise ResearchError('review_invocation_unverified', 'Select a ready authenticated scientific delivery')
    if assignment.get('staged_review', {}).get('kind') == 'controller':
        from .direction_reviews import collect_selected_task_graph
        graph = collect_selected_task_graph(records, evaluation, assignment_id)
        if not graph['completion']['complete']:
            raise ResearchError('review_invocation_unverified', 'Coverage requires the completed selected task graph')
        packets = [packet for _, packet in graph['packets']]
        if graph.get('frame_packet') is not None:
            packets.append(graph['frame_packet'])
        identities = [dict(assignment_id=child['id'],
            **{key: copy.deepcopy(packet.get(key, child.get(key))) for key in
               ('digest', 'protocol', 'role', 'reviewer_id', 'dossier_id', 'subject')})
            for child, packet in graph['packets']]
        return dict(assignment_id=assignment_id, attempt_id=None, request_digest=None, packet_identities=identities,
                    current_input=project_delivery_view(records, evaluation, []),
                    citation_history=project_delivery_view(records, evaluation, packets),
                    coverage_witnesses=project_delivery_view(records, evaluation, packets), task_graph=graph)
    attempt = records.get('review_attempt', {}).get(state.get('attempt_id'))
    if attempt is None:
        raise ResearchError('review_invocation_unverified', 'The selected result must bind its scientific attempt')
    delivery = copy.deepcopy(_read_authenticated_delivery(records, evaluation, assignment, attempt))
    for key in ('citation_history', 'coverage_witnesses'):
        delivery[key] = _read_session_delivery(records, evaluation, assignment, delivery[key])
    return delivery
