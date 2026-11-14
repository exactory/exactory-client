"""Admit a refused position's next turn and verify its immutable origin."""

import copy

from .errors import ResearchError
from .evidence import digest
from .operations import fields, immutable_record, normalized_text, text
from . import review_refusal_notice as notices

KEY = 'output_refusal'
INELIGIBLE_REPLACEMENT_MSG = 'Replace the current supported turn only after every observed output still fails a stored contract refusal.'
REPLACEMENT_IDENTITY_MSG = 'A refusal replacement keeps the role, dossier, subject, slot, reviewer, route, author and original context.'
REPLACEMENT_ATTEMPT_LIMIT_MSG = 'This refusal replacement has used its single output attempt; admit the next replacement if this position has one left.'
REPLACEMENT_LIMIT_MSG = 'This position has used its two replacements that carry a refusal.'
REPLACEMENT_ORIGIN_MSG = 'The refusal replacement must bind every earlier stored refusal and its exact completed output in this position.'
NOT_SELECTED_MSG = 'This output is not selected because a later assignment holds its position or slot.'


def read_position_context(assignment):
    """A refusal notice does not create another initial turn position."""
    return {key: value for key, value in assignment.get('context', {}).items() if key != KEY}


def find_output_successor(records, assignment):
    """Retain a later replacement, repair or new reviewer as the sole candidate."""
    from . import review_protocol as protocol
    replacement = protocol.find_replacement(records, assignment)
    if replacement is not None:
        return replacement
    slot = protocol.context_repair.slot_id(records, assignment)
    return next((later for later in records.get('review_assignment', {}).values()
        if later['recorded_revision'] > assignment['recorded_revision']
        and all(later.get(key) == assignment.get(key) for key in ('role', 'dossier_id'))
        and protocol.context_repair.is_same_subject(later, assignment)
        and protocol.context_repair.slot_id(records, later) == slot
        and (later.get('context', {}).get('context_repair')
             or not later.get('context', {}).get('prior_assignment_id')
             and normalized_text(later['reviewer_id']) != normalized_text(assignment['reviewer_id']))), None)


def require_selected_output(records, assignment):
    """Reject recording or continuing an earlier position after its successor exists."""
    successor = find_output_successor(records, assignment)
    if successor is not None:
        from .review_protocol import find_replacement
        code = 'review_assignment_replaced' if find_replacement(records, assignment) is not None else 'review_assignment_superseded'
        raise ResearchError(code, NOT_SELECTED_MSG, {'assignment_id': successor['id']})


def prepare_replacement(records, artifacts, value, revision):
    """Check admission once, then freeze a fresh request over the refused turn's packet."""
    from . import review_protocol as protocol, review_parts, review_prompt_paragraphs as paragraphs
    from . import review_refusal, review_route_capacity as capacity
    from .review_failure_class import list_turn_assignments
    binding = value['context'][KEY]
    fields(binding, ('assignment_id',), code='invalid_review_context')
    text(binding['assignment_id'], 'Refused assignment ID', code='invalid_review_context')
    prior = protocol._get(records, 'review_assignment', binding['assignment_id'])
    members = [records['review_assignment'][identifier] for identifier in list_turn_assignments(records, prior)]
    if max(members, key=lambda row: row['recorded_revision'])['id'] != prior['id']:
        raise ResearchError('review_assignment_replaced', NOT_SELECTED_MSG)
    protocol.context_repair.require_current_slot(records, prior)
    if protocol.describe_assignment_calls(records, prior)['pending']:
        raise ResearchError('review_invocation_pending', INELIGIBLE_REPLACEMENT_MSG)
    packet = protocol._read_json(artifacts, prior['packet'])
    if (prior.get('staged_review') or prior.get('reading_chain')
            or prior['role'] == 'native_math' and review_parts.KEY not in prior
            or prior['role'] not in protocol.bar_sources.ROLES + ('adjudicator',) and review_parts.KEY not in prior
            or prior['context_state']['context_status'] != 'verified_for_route'):
        raise ResearchError('review_refusal_replacement_ineligible', INELIGIBLE_REPLACEMENT_MSG)
    if (any(prior.get(key) != value.get(key) for key in ('role', 'dossier_id', 'subject', 'route_id'))
            or any(normalized_text(prior[key]) != normalized_text(value[key]) for key in ('reviewer_id', 'author_id'))
            or read_position_context(prior) != read_position_context(value)):
        raise ResearchError('review_refusal_replacement_mismatch', REPLACEMENT_IDENTITY_MSG)
    if sum(KEY in member.get('context', {}) for member in members) >= 2:
        raise ResearchError('review_slot_exhausted', REPLACEMENT_LIMIT_MSG)
    identifiers = {member['id'] for member in members}
    attempts = sorted((attempt for attempt in records.get('review_attempt', {}).values()
                       if attempt['assignment_id'] in identifiers), key=lambda row: row['recorded_revision'])
    refused_ids = [attempt['id'] for attempt in attempts if attempt['id'] in records.get('review_output_refusal', {})]
    diagnoses = {identifier: review_refusal.classify_observed_output(records, artifacts, identifier)
                 for identifier in refused_ids}
    completed = [attempt for attempt in attempts if attempt['assignment_id'] == prior['id']
                 and attempt['status'] == 'completed']
    if (not completed or len(refused_ids) > notices.REFUSED_ATTEMPT_LIMIT
            or any(not protocol._context_observed(artifacts, attempt)
                   or not protocol.authenticate_attempt(records, artifacts, prior, attempt)
                   or diagnoses.get(attempt['id']) is None for attempt in completed)):
        raise ResearchError('review_refusal_replacement_ineligible', INELIGIBLE_REPLACEMENT_MSG)
    packet.pop(KEY, None)
    base_prompt = paragraphs.strip_prompt_paragraphs(prior['prompt'], prior['role'])
    parameters = paragraphs.read_paragraph_parameters(prior['prompt'])
    versions = [version for version in parameters.get(paragraphs.PARAMETER, [])
                if version not in paragraphs.REFUSAL_PARAGRAPH_VERSIONS.values()]
    def render_prompt(form):
        return paragraphs.append_prompt_paragraphs(base_prompt, prior['role'],
            parameters.get(paragraphs.CARRY_PARAMETER), versions + [paragraphs.REFUSAL_PARAGRAPH_VERSIONS[form]],
            parameters.get(paragraphs.CAPACITY_PARAMETER))
    route = records['review_route'][prior['route_id']]
    latest = completed[-1]
    task = latest.get('task') or capacity.describe_task(prior['role'], packet, route=route,
        request=protocol._read_json(artifacts, latest['request']))
    limit = capacity._find_task_level(task['characters'])
    selection = notices.select_refusal_notice(records, artifacts, refused_ids, diagnoses=diagnoses,
        route=route, role=prior['role'], prompt=render_prompt, packet=packet,
        task_characters=None, task_limit_characters=limit)
    if selection['notice'] is None:
        raise ResearchError('review_input_too_large', notices.NOTICE_DELIVERY_MSG, {'trials': selection['trials']})
    packet[KEY] = selection['notice']
    packet.pop('digest', None)
    packet['digest'] = digest(packet)
    prompt = selection['prompt']
    record = copy.deepcopy(prior)
    for key in ('history_ref', 'history', 'review_parts_restart'):
        record.pop(key, None)
    record.update(value, packet=protocol._put_json(artifacts, packet), packet_digest=packet['digest'],
        prompt=prompt, prompt_hash=prompt['rendered_prompt_hash'], history=[],
        recorded_revision=revision)
    # Packet identity keeps the original spellings of these normalized identities.
    record.update({key: prior[key] for key in ('reviewer_id', 'author_id')})
    # The scientific parent stays the same. The replaced turn is counted separately for citations.
    record['slot_id'] = protocol.context_repair.slot_id(records, prior)
    return [immutable_record(records, 'review_assignment', value['id'], record)], record


def validate_replacement_origin(records, artifacts, assignment, packet=None):
    """Verify every frozen entry, without a validator or a present-day refusal decision."""
    from . import review_protocol as protocol
    from .review_failure_class import list_turn_assignments
    binding = assignment.get('context', {}).get(KEY)
    if binding is None:
        return
    def require(condition):
        if not condition:
            raise ResearchError('review_refusal_replacement_origin_invalid', REPLACEMENT_ORIGIN_MSG)
    packet = protocol._read_json(artifacts, assignment['packet']) if packet is None else packet
    prior = records.get('review_assignment', {}).get(binding['assignment_id'])
    require(prior is not None and prior['recorded_revision'] < assignment['recorded_revision'])
    require(read_position_context(prior) == read_position_context(assignment))
    require(all(prior.get(key) == assignment.get(key) for key in ('role', 'dossier_id', 'subject', 'route_id')))
    require(protocol.context_repair.slot_id(records, prior) == protocol.context_repair.slot_id(records, assignment))
    require(all(normalized_text(prior[key]) == normalized_text(assignment[key]) for key in ('reviewer_id', 'author_id')))
    notice = packet.get(KEY, {})
    entries = notice.get('refused', [])
    require(notice.get('protocol') == notices.PROTOCOL and notice.get('form') in ('whole', 'bounded')
            and isinstance(entries, list) and 1 <= len(entries) <= notices.REFUSED_ATTEMPT_LIMIT)
    position = set(list_turn_assignments(records, prior))
    certificates = sorted((certificate for certificate in records.get('review_output_refusal', {}).values()
        if certificate['assignment_id'] in position and certificate['recorded_revision'] < assignment['recorded_revision']),
        key=lambda row: row['recorded_revision'])
    require([entry.get('attempt_id') for entry in entries] == [row['attempt_id'] for row in certificates])
    for entry, certificate in zip(entries, certificates):
        attempt = records.get('review_attempt', {}).get(entry['attempt_id'])
        require(entry.get('status') in ('refused', 'not_refused'))
        identity = {'assignment_id', 'attempt_id', 'output_digest', 'refusal_digest', 'status'}
        require(set(entry) == (identity if entry['status'] == 'not_refused' else
                              identity | {'code', 'check', 'path', 'message', 'details'}))
        require(attempt is not None and attempt['status'] == 'completed'
                and attempt['recorded_revision'] < certificate['recorded_revision']
                and attempt['assignment_id'] == certificate['assignment_id'] == entry.get('assignment_id')
                and certificate['attempt_id'] == entry['attempt_id']
                and certificate['output_digest'] == entry.get('output_digest')
                and digest(certificate) == entry.get('refusal_digest')
                and digest(protocol._read_json(artifacts, attempt['output'])) == entry['output_digest']
                and attempt['request_digest'] == certificate['request_digest'])
    require(entries[-1]['assignment_id'] == prior['id'] and entries[-1]['status'] == 'refused')
