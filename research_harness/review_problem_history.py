"""Omit authenticated earlier result outputs only after the part planner refuses."""

import json

from . import review_context_repair, review_problem_parts as parts, review_transport as transport
from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from .operations import normalized_text

PROTOCOL = 'exactory-problem-result-history-v1'
DELIVERY = 'output-elided-from-replayed-history'
PART_OUTPUT_NOTE = (
    'Harness placeholder, not your text: this part output from an earlier series is omitted from the replay to save '
    'space. The original output stays in the record. review_id identifies the recorded review that closed that '
    'series. That review is delivered in the packet field historical_reviews. The omission resolves no objection. '
    'The current series must assess its own evidence in full.')
FINAL_OUTPUT_NOTE = (
    'Harness placeholder, not your text: this earlier final output is omitted from the replay to save space. The '
    'original output stays in the record. The recorded review identified by review_id is delivered in the packet '
    'field historical_reviews. later_review_id identifies a later recorded review whose final output was retained '
    'in full in the replay when this placeholder was created. The omission resolves no objection. The current series '
    'must assess its own evidence in full.')


def _walk_ancestors(records, assignment_id):
    chain, seen = [], set()
    while assignment_id is not None:
        if assignment_id in seen or assignment_id not in records.get('review_assignment', {}):
            return None
        seen.add(assignment_id)
        assignment = records['review_assignment'][assignment_id]
        chain.append(assignment)
        assignment_id = assignment.get('history_assignment_id')
    return list(reversed(chain))


def _read_object(content):
    """Return a parsed JSON object, or None for invalid JSON or another JSON type."""
    try:
        value = json.loads(content)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _map_history(records, artifacts, chain, history):
    """Return positional bindings, or None when no compaction is permitted."""
    from .review_protocol import assignment_state
    if chain is None or not isinstance(history, list) or len(history) != 2 * len(chain):
        return None
    ancestors = {row['id'] for row in chain}
    mapped = []
    for index, assignment in enumerate(chain):
        user, assistant = history[2 * index:2 * index + 2]
        if not isinstance(user, dict) or not isinstance(assistant, dict):
            return None
        stored = assignment.get('history', [])
        content, output = user.get('content'), assistant.get('content')
        if (not isinstance(stored, list) or len(stored) != 2 * index or user.get('role') != 'user'
                or assistant.get('role') != 'assistant' or not isinstance(content, str)
                or not isinstance(output, str) or not content.startswith(transport.PACKET_LABEL)):
            return None
        packet = _read_object(content[len(transport.PACKET_LABEL):])
        if packet is None or packet.get('digest', packet.get('packet_digest')) != assignment['packet_digest']:
            return None
        frozen = _read_object(artifacts.read(assignment['packet']))
        if frozen is None:
            return None
        plan = frozen.get(parts.KEY)
        if parts.KEY in frozen or 'evidence_parts' in assignment['prompt'].get('parameters', {}):
            if (not isinstance(plan, dict) or parts.find_stage(frozen) is None
                    or not isinstance(plan.get('series_assignment_id'), str) or type(plan.get('turn')) is not int):
                return None
        state = assignment_state(records, artifacts, assignment['id'])
        if state['output'] is None:
            return None
        placeholder = output != json.dumps(state['output'], sort_keys=True)
        if placeholder:
            omitted = _read_object(output)
            if (omitted is None or not isinstance(plan, dict)
                    or omitted.get('protocol') != PROTOCOL or omitted.get('delivery') != DELIVERY
                    or omitted.get('assignment_id') != assignment['id']
                    or omitted.get('output_digest') != digest(state['output'])
                    or omitted.get('series_assignment_id') != plan.get('series_assignment_id')
                    or omitted.get('turn') != plan.get('turn')
                    or omitted.get('evidence_stage') != parts.find_stage(frozen)
                    or not isinstance(omitted.get('review_id'), str)):
                return None
            review = records.get('value_review', {}).get(omitted['review_id'])
            if review is None:
                return None
            closing = records['review_assignment'].get(review['assignment_id'])
            if closing is None or closing['id'] not in ancestors:
                return None
            closing_packet = _read_object(artifacts.read(closing['packet']))
            closing_plan = closing_packet.get(parts.KEY) if closing_packet is not None else None
            if (not isinstance(closing_plan, dict) or parts.find_stage(closing_packet) != 'final'
                    or closing_plan.get('series_assignment_id') != plan.get('series_assignment_id')
                    or plan.get('stage') == 'final' and closing['id'] != assignment['id']):
                return None
        mapped.append((assignment, frozen, state, placeholder))
    return mapped


def _compact_history(records, artifacts, value, packet, history, mapped):
    """Return history with shorter eligible outputs replaced, or the unchanged history."""
    from .review_protocol import deliver_review
    parent = mapped[-1][0]
    slot = review_context_repair.slot_id(records, parent)
    previous = mapped[-1][1] if value['context'].get('prior_assignment_id') else {}
    refreshed = any(review['assignment_id'] == parent['id'] for review in records.get('value_review', {}).values())
    continuing = (parts.find_stage(previous) is not None and not refreshed
                  and previous.get('problem_evidence_digest') == packet['problem_evidence_digest'])
    current_series = previous[parts.KEY]['series_assignment_id'] if continuing else value['id']
    recorded = []
    for assignment, frozen, state, _ in mapped:
        if (assignment['role'] != 'result' or assignment['subject'] != packet['subject']
                or assignment['route_id'] != value['route_id'] or assignment['session_id'] != parent['session_id']
                or review_context_repair.slot_id(records, assignment) != slot
                or any(normalized_text(assignment[key]) != normalized_text(value[key])
                       for key in ('reviewer_id', 'author_id')) or not state['ready']):
            continue
        reviews = [review for review in records.get('value_review', {}).values()
                   if review['assignment_id'] == assignment['id']]
        if len(reviews) != 1:
            continue
        review = reviews[0]
        response = {key: content for key, content in review['payload'].items()
                    if key not in ('id', 'dossier_id', 'assignment_id')}
        if state['output_digest'] == review['output_digest'] and state['output'] == response:
            recorded.append((assignment, frozen, review))
    if not recorded:
        return history
    latest_id = recorded[-1][2]['id']
    ancestors = {row[0]['id'] for row in mapped}
    eligible = {}
    for assignment, frozen, review in recorded:
        if (parts.find_stage(frozen) != 'final'
                or frozen[parts.KEY]['series_assignment_id'] == current_series
                or deliver_review(records, review['payload']) not in packet.get('historical_reviews', [])):
            continue
        series = parts._list_series(records, artifacts, assignment['id'])
        identifiers = [row['id'] for row, _ in series]
        if not identifiers or not set(identifiers) <= ancestors:
            continue
        observed = parts.observe_series(records, artifacts, assignment['id'])
        if identifiers != [row['receipt']['assignment_id'] for row in observed]:
            continue
        for member, member_packet in series:
            if parts.find_stage(member_packet) == 'part':
                eligible[member['id']] = (review['id'], None)
        if review['id'] != latest_id:
            eligible[assignment['id']] = (review['id'], latest_id)
    reduced = list(history)
    for index, (assignment, frozen, state, placeholder) in enumerate(mapped):
        if placeholder or assignment['id'] not in eligible:
            continue
        review_id, later_id = eligible[assignment['id']]
        plan = frozen[parts.KEY]
        omitted = {'protocol': PROTOCOL, 'delivery': DELIVERY, 'assignment_id': assignment['id'],
                   'series_assignment_id': plan['series_assignment_id'], 'turn': plan['turn'],
                   'evidence_stage': plan['stage'], 'output_digest': state['output_digest'],
                   'review_id': review_id, 'note': PART_OUTPUT_NOTE if plan['stage'] == 'part' else FINAL_OUTPUT_NOTE}
        if later_id is not None:
            omitted['later_review_id'] = later_id
        content = json.dumps(omitted, sort_keys=True, ensure_ascii=True, allow_nan=False)
        position = 2 * index + 1
        if len(content) < len(history[position]['content']):
            reduced[position] = dict(history[position], content=content)
    return reduced


def bound_history(records, artifacts, value, route, packet, prompt, history, history_assignment_id):
    """Return unchanged history, or a smaller replay after legacy planning refuses."""
    if (value['role'] != 'result' or packet.get('subject', {}).get('kind') != 'problem'
            or route['adapter'] not in ('claude_cli_v1', 'codex_cli_v1', 'codex_cli_framed_v1')
            or history_assignment_id is None or 'context_repair' in packet or 'complete_source_delivery' in packet
            or value['context'].get('source_delivery') or 'problem_evidence' not in packet
            or packet['problem_evidence'].get('delivery') == 'delivered-in-an-earlier-turn'):
        return history
    chain = _walk_ancestors(records, history_assignment_id)
    if chain is not None:
        if any(row['context'].get('source_delivery') or 'context_repair' in row['context'] for row in chain):
            return history
        if not any(review['assignment_id'] in {row['id'] for row in chain if row['role'] == 'result'}
                   for review in records.get('value_review', {}).values()):
            return history
    artifacts = Evaluation.of(records, artifacts)
    try:
        parts.prepare_packet(records, artifacts, value, route, packet, prompt, history)
    except ResearchError as refusal:
        if refusal.code != 'review_input_too_large':
            raise
        try:
            mapped = _map_history(records, artifacts, chain, history)
            reduced = (_compact_history(records, artifacts, value, packet, history, mapped)
                       if mapped else history)
        except ResearchError:
            raise refusal
        if reduced == history:
            raise
        return reduced
    return history
