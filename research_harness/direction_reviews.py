"""Verify direction evidence in actual independent review inputs."""

import copy
import json

from .direction_sources import find_delivered_readings
from .evaluation import Evaluation
from .graph import obligation
from .review_context_repair import is_same_subject
from .review_protocol import assignment_state, describe_direction_evidence


def collect_selected_task_graph(records, artifacts, assignment_id):
    """Reuse the verified selected graph only within its exact evaluation scope."""
    from .review_state_evaluation import evaluate_state
    return evaluate_state(records, artifacts, 'selected_task_graph', assignment_id, _collect_selected_task_graph)


def _collect_selected_task_graph(records, artifacts, assignment_id):
    """Verify the epoch's explicit accepted selection and recover its actual inputs.

    Task acceptance receipts bind staged_observation and attempt. This traverses
    every selected sibling, without giving predecessor reading credit to a fresh
    reviewer. Completion is the shared verifier's result, including partial work.
    """
    from .errors import ResearchError
    from .evidence import digest
    from . import review_protocol as protocol, review_staged as staged
    from .review_excerpts import read_sent_packet
    from .review_input_projection import collect_attachments, project_evidence
    from .review_transport import logical_messages
    evaluation = Evaluation.of(records, artifacts)
    assignment = records['review_assignment'][assignment_id]
    binding = assignment['staged_review']
    epoch = records['review_epoch'][binding['epoch_id']]
    controller_id = epoch['controller_assignment_id']
    from .review_staged_protocol import list_epoch_tasks
    tasks = list_epoch_tasks(records, epoch)
    observations, packets = [], []
    for acceptance in records.get('review_task_acceptance', {}).values():
        receipt = acceptance['receipt']
        observed, attempt = receipt.get('staged_observation'), receipt.get('attempt')
        if observed is None or observed.get('epoch_id') != epoch['id'] or attempt is None:
            continue
        child = records.get('review_assignment', {}).get(attempt['assignment_id'])
        if child is None or not protocol.authenticate_attempt(records, evaluation, child, attempt):
            raise ResearchError('review_invocation_unverified', 'Authenticate every selected task observation.')
        raw = protocol.read_raw_observation(records, evaluation, attempt['id'])
        if any(row['assignment_id'] == child['id'] for row in
               records.get('review_output_correction_invocation', {}).values()):
            from .review_output_correction import selected_correction
            correction = selected_correction(records, evaluation, child['id'])
            if correction is not None and correction.get('requires_reauthorization'):
                raise ResearchError('review_composition_pending',
                    'A raw task correction invalidates its dependent composition; authorize a new root.',
                    {'attempt_id': attempt['id'], 'output_correction_id': correction['id']})
        binder = getattr(staged, 'bind_quotations', None)
        bound, _ = (binder(raw['raw_output'], epoch=epoch, sources=epoch['manifest']['sources'])
                    if binder is not None else (raw['raw_output'], []))
        contexts = {key:dict(context, root=child['staged_task']['context_roots'].get(key, context['root']))
                    for key,context in epoch['manifest']['contexts'].items()}
        accepted_output = staged.validate_task_output(child['staged_task'], bound,
            manifest=dict(epoch['manifest'], contexts=contexts), validate_records=False)
        if (not raw['instrumented'] or raw['status'] != 'completed'
                or digest(raw['raw_output']) != raw['raw_output_digest']
                or accepted_output != observed['output']
                or digest(accepted_output) != observed['raw_output_digest']
                or raw['raw_output_digest'] != receipt.get('raw_output_digest', raw['raw_output_digest'])
                or raw['request_digest'] != observed['request_digest']):
            raise ResearchError('review_invocation_unverified', 'The receipt must bind its exact raw capture and request.')
        request = json.loads(evaluation.read(raw['request']))
        packet = read_sent_packet(request)
        if (packet is None or packet.get('staged_review', {}).get('epoch_id') != epoch['id']
                or packet['staged_review'].get('task', {}).get('id') != observed['task_id']):
            raise ResearchError('review_invocation_unverified', 'A selected task needs its exact actual packet.')
        content = logical_messages(request)[-1]['content']
        project_evidence(packet, collect_attachments(content) if isinstance(content, list) else {})
        originals = packet['staged_review'].get('originals', [])
        spans = [row['span'] for row in originals]
        if any(span not in spans for span in observed['actual_input']['intervals']):
            raise ResearchError('review_invocation_unverified', 'Selected source intervals must have reached their task.')
        if observed['actual_input']['binary_sources']:
            from .review_staged_binary import read_actual_sources
            delivered = read_actual_sources(records, evaluation, epoch, child['staged_task'], request)
            if any(row not in delivered for row in observed['actual_input']['binary_sources']):
                raise ResearchError('review_invocation_unverified', 'Selected native bodies must have reached their task.')
        sources = {source['id']: source for source in epoch['manifest']['sources']}
        for original in originals:
            span = original['span']
            if staged.materialize_quotation(sources[span['source_id']], span)['text'] != original['text']:
                raise ResearchError('review_invocation_unverified', 'Actual original text must match its frozen source.')
        observations.append(observed)
        packets.append((child, packet))
    selection = {'observations': observations}
    state = protocol.assignment_state(records, evaluation, controller_id)
    effective = state.get('effective') or {}
    if effective.get('kind') == 'paged':
        selection['composition_certificate'] = dict(complete=True, epoch_digest=epoch['digest'],
            composition_root=effective['composition_root'],
            selected_observations_root=effective['selected_observations_root'])
    completion = staged.verify_completion(epoch, selection, tasks=tasks)
    if effective.get('selected_observations_root') not in (None, completion['selected_observations_root']):
        raise ResearchError('research_review_binding_stale', 'The effective result selects a different task graph.')
    if effective.get('completion_root') not in (None, completion['digest']):
        raise ResearchError('research_review_binding_stale', 'The effective completion binding has changed.')
    from .review_composition import validate_page_grounding
    for observed, (_, packet) in zip(observations, packets):
        for page in observed['output'].get('pages', []):
            validate_page_grounding(page, epoch=epoch, selection=selection, tasks=tasks,
                actual_input=observed['actual_input'], delivered=packet['staged_review'])
    frame_packet = None
    coverage = {row['source_id']: row['complete'] for row in completion['coverage']}
    if completion['complete'] and all(coverage.get(row['source_id'], False) for row in epoch['manifest']['frame_fields']):
        controller = records['review_assignment'][controller_id]
        if controller.get('packet') is not None:
            # The original fields have all reached the selected graph. Their
            # deterministic projection supplies record identity and alias scope,
            # while body delivery still comes only from actual task inputs.
            frame_packet = copy.deepcopy(json.loads(evaluation.read(controller['packet'])))
            frame_packet['evidence'] = []
    return dict(epoch=epoch, tasks=tasks, selection=selection, completion=completion,
                effective=effective, packets=packets, frame_packet=frame_packet)


def collect_observed_packets(records, artifacts, assignment_id):
    """Collect only observed inputs in the review's bound scientific ancestry."""
    pending, seen, packets = [assignment_id], set(), []
    while pending:
        identifier = pending.pop()
        if identifier in seen:
            continue
        seen.add(identifier)
        assignment = records.get("review_assignment", {}).get(identifier)
        if assignment is None:
            continue
        if assignment.get('staged_review') is not None:
            graph = collect_selected_task_graph(records, artifacts, identifier)
            packets.extend(graph['packets'])
            if graph.get('frame_packet') is not None:
                controller = records['review_assignment'][graph['epoch']['controller_assignment_id']]
                packets.append((controller, graph['frame_packet']))
            continue
        state = assignment_state(records, artifacts, identifier)
        invalid = {"review_invocation_unverified", "review_output_unavailable", "review_output_pending"}
        if state["output"] is not None and not any(item["code"] in invalid for item in state["obligations"]):
            packets.append((assignment, json.loads(artifacts.read(assignment["packet"]))))
        parent = assignment.get("history_assignment_id") or assignment.get("context", {}).get("prior_assignment_id")
        if parent is not None:
            pending.append(parent)
        repair = assignment.get("context", {}).get("context_repair")
        if isinstance(repair, dict) and repair.get("assignment_id") is not None:
            pending.append(repair["assignment_id"])
        elif parent is None and assignment["role"] == "slate" and assignment.get("session_id") != identifier:
            pending.append(assignment.get("session_id"))
    return packets


def _matches_bound_input_ancestry(records, assignment_id, packets):
    """Check identities from the bound ancestry without recovering its packet bodies."""
    pending, seen, ancestors = [assignment_id], set(), []
    while pending:
        identifier = pending.pop()
        if identifier in seen:
            continue
        seen.add(identifier)
        assignment = records.get('review_assignment', {}).get(identifier)
        if assignment is None:
            return False
        ancestors.append(assignment)
        parent = assignment.get('history_assignment_id') or assignment.get('context', {}).get('prior_assignment_id')
        if parent is not None:
            pending.append(parent)
        repair = assignment.get('context', {}).get('context_repair')
        if isinstance(repair, dict) and repair.get('assignment_id') is not None:
            pending.append(repair['assignment_id'])
        elif parent is None and assignment.get('session_id') not in (None, identifier):
            pending.append(assignment['session_id'])
    # A context repair keeps the bar subject of the packet that it reassesses, which can predate the statement.
    return all(any(packet.get('digest') == assignment.get('packet_digest')
                   and all(packet.get(key) == assignment.get(key) for key in ('role', 'reviewer_id', 'dossier_id'))
                   and is_same_subject(packet, assignment)
                   for assignment in ancestors) for packet in packets)


def collect_actual_input_packets(records, artifacts, assignment_id):
    """The selected observed request alone supplies the final problem review's input.

    Its logical history is already present in that request. Earlier saved packets
    and requests cannot restore content absent from this actual invocation.
    """
    from .errors import ResearchError
    from .review_input_projection import packets_from_request, packets_from_visual_request
    assignment = records.get('review_assignment', {}).get(assignment_id)
    if assignment is None:
        return []
    if assignment.get('staged_review') is not None:
        return [packet for _, packet in collect_selected_task_graph(records, artifacts, assignment_id)['packets']]
    state = assignment_state(records, artifacts, assignment_id)
    if not state['ready']:
        return []
    attempt = records.get('review_attempt', {}).get(state.get('attempt_id'))
    if attempt is None or attempt.get('origin') != 'instrumented':
        return []
    route = records['review_route'][assignment['route_id']]
    try:
        request = json.loads(artifacts.read(attempt['request']))
        packets = (packets_from_visual_request(request) if route['adapter'] == 'codex_cli_visual_v1' else
                   packets_from_request(request, native_attachments=route['adapter'] == 'openai_responses_v1'))
        return packets if _matches_bound_input_ancestry(records, assignment_id, packets) else []
    except (ResearchError, ValueError, TypeError, KeyError, UnicodeError):
        # The caller's existing coverage gate reports the missing evidence.
        # Authentication and decoding failures never fall back to a saved packet.
        return []


def assess_direction_reviews(records, artifacts, dossier, reviews, direction):
    """Require both observed reviewers to cover the same decisive evidence and claim."""
    evaluation = Evaluation.of(records, artifacts)
    expected, _ = describe_direction_evidence(records, evaluation, direction, dossier)
    readings = {item["id"]: item for item in expected["readings"]}
    obligations = []
    for review in reviews:
        covered = set()
        packets = collect_observed_packets(records, evaluation, review["assignment_id"])
        delivered = find_delivered_readings(records, evaluation, [records["reading"][identifier] for identifier in readings],
                                            [packet for _, packet in packets])
        missing_readings = set(readings) - delivered
        for assignment, packet in packets:
            supplied = packet.get("direction_evidence", {})
            for key in ("claim", "search", "standards", "grand_challenge"):
                if expected[key] is not None and supplied.get(key) == expected[key]:
                    covered.add(key)
        missing = sorted({"claim", "search", "standards", "grand_challenge"} - covered)
        if missing or missing_readings:
            obligations.append(obligation("direction_evidence_unassessed", "Obtain an observed assessment or bound supplement covering the decisive direction evidence and claim.",
                review_id=review["id"], missing=missing, reading_ids=sorted(missing_readings)))
    return obligations
