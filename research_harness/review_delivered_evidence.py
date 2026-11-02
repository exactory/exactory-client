"""Derive separate delivery views from exact authenticated review invocations."""

import copy

from .errors import ResearchError
from .evaluation import Evaluation
from . import review_protocol as protocol, review_problem_parts as parts
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


def read_attempt_delivery(records, artifacts, assignment, attempt):
    """Authenticate one named completed attempt without selecting its current state.

    Each view contains packets, artifacts, source_mappings and carried_records.
    current_input is exactly the named request. citation_history additionally
    admits only the legacy part contract's historical deliveries. Coverage uses
    the current part series alone. Authentication or decoding failure raises an
    existing review error and supplies no saved-packet fallback.
    """
    evaluation = Evaluation.of(records, artifacts)
    if (records.get('review_assignment', {}).get(assignment.get('id')) != assignment
            or records.get('review_attempt', {}).get(attempt.get('id')) != attempt
            or attempt.get('assignment_id') != assignment.get('id')
            or attempt.get('status') != 'completed'
            or not protocol.authenticate_attempt(records, evaluation, assignment, attempt)):
        raise ResearchError('review_invocation_unverified', 'Delivery requires the exact authenticated completed attempt')
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
                                            assignments=ancestors))
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
        identities = []
        for packet in packets:
            owner = next(owner for owner in ancestors if owner['packet_digest'] == packet['digest'])
            identities.append(dict(assignment_id=owner['id'],
                **{key: copy.deepcopy(packet.get(key, owner.get(key))) for key in
                   ('digest', 'protocol', 'role', 'reviewer_id', 'dossier_id', 'subject')}))
        return dict(assignment_id=assignment['id'], attempt_id=attempt['id'], request_digest=raw['request_digest'],
                    packet_identities=identities, current_input=current,
                    citation_history=project_delivery_view(records, evaluation, citation),
                    coverage_witnesses=project_delivery_view(records, evaluation, coverage))
    except ResearchError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError, OSError) as error:
        raise ResearchError('review_input_unreadable', 'The exact authenticated input cannot be decoded') from error


def read_selected_delivery(records, artifacts, assignment_id):
    """Select at a gate boundary, retaining a metadata correction's original attempt.

    A staged controller has no scientific invocation of its own. Its accepted
    task graph supplies coverage and controller citations; explicit task reads
    above remain confined to that task's input, including its bound dependencies.
    """
    evaluation = Evaluation.of(records, artifacts)
    assignment = records.get('review_assignment', {}).get(assignment_id)
    state = protocol.assignment_state(records, evaluation, assignment_id)
    if assignment is None or not state['ready']:
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
    return read_attempt_delivery(records, evaluation, assignment, attempt)
