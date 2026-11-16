"""Admit a refused position's next turn and verify its immutable origin."""

import copy

from .errors import ResearchError
from .evidence import digest
from .operations import fields, immutable_record, normalized_text, text
from . import review_refusal_notice as notices

KEY = 'output_refusal'
INELIGIBLE_REPLACEMENT_MSG = (
    'This assignment does not admit a replacement that carries a refusal. Such a replacement needs the head of a '
    'turn that is not staged and has a verified context: a reading turn or a final turn of a chain of '
    'exactory-openness-reading-v7, a turn in the role bar, slate, result or adjudicator, or a turn of a series in '
    'parts. A reading chain of an earlier protocol admits none. The head has no pending call and no pending or '
    'accepted correction. It needs at least one completed output with an observed context, and each such output '
    'must authenticate and still be refused. The position holds at most four refusal records. Run review-series on '
    'the head and take the step that it plans.')
REPLACEMENT_IDENTITY_MSG = (
    'The replacement does not keep the role, dossier, subject, route, reviewer, author and context of the refused '
    'assignment. Repeat these values of the refused assignment, and add only context.output_refusal.')
REPLACEMENT_ATTEMPT_LIMIT_MSG = (
    'A replacement that carries a refusal has one output, and the output of this replacement was refused. Run '
    'review-series on the head. It plans another replacement that carries the refusal when one is admissible, and '
    'the step of the role of the head otherwise.')
REPLACEMENT_LIMIT_MSG = (
    'This position already holds two replacements that carry a refusal, so it admits no third. Run review-series '
    'on the head and take the step that it plans.')
REPLACEMENT_ORIGIN_MSG = (
    'The stored records do not verify the origin of this replacement. Its notice must list every refusal that the '
    'position held before the replacement, each with the digest of its record and of its completed output. Take '
    'the step that review-series plans for the head.')
NOT_SELECTED_MSG = (
    'A later assignment holds the position or the slot of this assignment, so the harness does not select this '
    'output and does not continue this assignment. Continue with the head of the position, which review-series '
    'resolves from any assignment of the chain.')


def read_position_context(assignment):
    """A refusal notice does not create another initial turn position."""
    return {key: value for key, value in assignment.get('context', {}).items() if key != KEY}


def read_replacement_link(assignment):
    """A capacity link takes precedence over the refusal context it copied."""
    if 'capacity_replacement_of' in assignment:
        return 'capacity', assignment['capacity_replacement_of']
    refusal = assignment.get('context', {}).get(KEY)
    return ('output_refused', refusal.get('assignment_id')) if refusal else ('capacity', None)


def find_output_successor(records, assignment):
    """Retain a later replacement, repair or new reviewer as the sole candidate."""
    from . import review_protocol as protocol
    replacement = protocol.find_replacement(records, assignment)
    if replacement is not None:
        return replacement
    slot = protocol.context_repair.slot_id(records, assignment)
    return next((later for later in records.get('review_assignment', {}).values()
        if later.get('recorded_revision', 0) > assignment.get('recorded_revision', 0)
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
    capacity_prior = records.get('review_assignment', {}).get(value['context'].get('reading_replacement'))
    if capacity_prior is not None and capacity_prior.get('context', {}).get(KEY) == binding:
        if (any(capacity_prior.get(key) != value.get(key) for key in ('role', 'dossier_id', 'subject', 'route_id'))
                or any(normalized_text(capacity_prior[key]) != normalized_text(value[key])
                       for key in ('reviewer_id', 'author_id'))):
            raise ResearchError('review_refusal_replacement_mismatch', REPLACEMENT_IDENTITY_MSG)
        from .openness_reading_v7 import prepare_notice_capacity_assignment
        return prepare_notice_capacity_assignment(records, artifacts, value, revision, capacity_prior)
    prior = protocol._get(records, 'review_assignment', binding['assignment_id'])
    members = [records['review_assignment'][identifier] for identifier in list_turn_assignments(records, prior)]
    if max(members, key=lambda row: row['recorded_revision'])['id'] != prior['id']:
        raise ResearchError('review_assignment_replaced', NOT_SELECTED_MSG)
    protocol.context_repair.require_current_slot(records, prior)
    if (protocol.describe_assignment_calls(records, prior)['pending']
            or any(row['assignment_id'] == prior['id'] and row['id'] not in records.get('review_output_correction', {})
                   for row in records.get('review_output_correction_invocation', {}).values())):
        raise ResearchError('review_invocation_pending', INELIGIBLE_REPLACEMENT_MSG)
    from .review_output_correction import selected_correction
    if selected_correction(records, artifacts, prior['id']) is not None:
        raise ResearchError('review_refusal_replacement_ineligible', INELIGIBLE_REPLACEMENT_MSG)
    packet = protocol._read_json(artifacts, prior['packet'])
    from . import openness_reading_v7 as v7
    is_v7 = protocol.openness_reading.find_protocol(packet) == v7.PROTOCOL
    if (prior.get('staged_review') or prior.get('reading_chain') and not is_v7
            or prior['role'] == 'native_math' and review_parts.KEY not in prior and not is_v7
            or prior['role'] not in protocol.bar_sources.ROLES + ('adjudicator',) and review_parts.KEY not in prior and not is_v7
            or prior['context_state']['context_status'] != 'verified_for_route'):
        raise ResearchError('review_refusal_replacement_ineligible', INELIGIBLE_REPLACEMENT_MSG)
    if (any(prior.get(key) != value.get(key) for key in ('role', 'dossier_id', 'subject', 'route_id'))
            or any(normalized_text(prior[key]) != normalized_text(value[key]) for key in ('reviewer_id', 'author_id'))
            or read_position_context(prior) != read_position_context(value)):
        raise ResearchError('review_refusal_replacement_mismatch', REPLACEMENT_IDENTITY_MSG)
    if sum(read_replacement_link(member)[0] == 'output_refused' for member in members) >= 2:
        raise ResearchError('review_slot_exhausted', REPLACEMENT_LIMIT_MSG)
    identifiers = {member['id'] for member in members}
    attempts = sorted((attempt for attempt in records.get('review_attempt', {}).values()
                       if attempt['assignment_id'] in identifiers), key=lambda row: row['recorded_revision'])
    refused_ids = [attempt['id'] for attempt in attempts if attempt['id'] in records.get('review_output_refusal', {})]
    diagnoses = {identifier: review_refusal.classify_observed_output(records, artifacts, identifier)
                 for identifier in refused_ids}
    completed = [attempt for attempt in attempts if attempt['assignment_id'] == prior['id']
                 and attempt['status'] == 'completed' and protocol._context_observed(artifacts, attempt)]
    if (not completed or len(refused_ids) > notices.REFUSED_ATTEMPT_LIMIT
            or any(not protocol._context_observed(artifacts, attempt)
                   or not protocol.authenticate_attempt(records, artifacts, prior, attempt)
                   or diagnoses.get(attempt['id']) is None for attempt in completed)):
        raise ResearchError('review_refusal_replacement_ineligible', INELIGIBLE_REPLACEMENT_MSG)
    if prior.get('session_chain'):
        from .review_session_v4 import prepare_refusal_assignment
        return prepare_refusal_assignment(records, artifacts, value, revision, prior, packet, refused_ids, diagnoses)
    if packet.get('review_parts', {}).get('protocol') == 'exactory-review-parts-v3':
        from .review_parts_v3 import prepare_refusal_assignment
        return prepare_refusal_assignment(records, artifacts, value, revision, prior, packet, refused_ids, diagnoses)
    if is_v7:
        return v7.prepare_refusal_assignment(records, artifacts, value, revision, prior, packet,
                                             refused_ids, diagnoses)
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
    forms = ('whole', 'bounded')
    from . import review_output_correction as correction
    if any(row['assignment_id'] == prior['id']
           for row in records.get('review_output_correction_invocation', {}).values()):
        forms = ('bounded',)
    elif (prior['role'] == 'result' and len(completed) == 2
          and diagnoses[latest['id']]['code'] == 'review_reassessment_mismatch'
          and diagnoses[latest['id']]['check'] == 'review.reassessment'):
        try:
            correction.build_request(records, artifacts, prior['id'], prior['route_id'])
        except ResearchError as error:
            if error.code == 'review_input_too_large':
                forms = ('bounded',)
    selection = notices.select_refusal_notice(records, artifacts, refused_ids, diagnoses=diagnoses,
        route=route, role=prior['role'], prompt=render_prompt, packet=packet,
        task_characters=None, task_limit_characters=limit, forms=forms)
    if selection['notice'] is None:
        from .review_failure_class import plan_capacity_recovery, plan_output_replacement
        recovery = plan_capacity_recovery(records, artifacts, prior, 'input_over_limit')
        if recovery['kind'] == 'replace':
            lower = plan_output_replacement(records, artifacts, prior, include_packet=True,
                                            outcome='input_over_limit', replacement_id=value['id'])
            if lower is not None and lower['state'] == 'replace':
                packet = lower['packet']
                base_prompt = paragraphs.strip_prompt_paragraphs(lower['prompt'], prior['role'])
                parameters = paragraphs.read_paragraph_parameters(lower['prompt'])
                versions = [version for version in parameters.get(paragraphs.PARAMETER, [])
                            if version not in paragraphs.REFUSAL_PARAGRAPH_VERSIONS.values()]
                selection = notices.select_refusal_notice(records, artifacts, refused_ids, diagnoses=diagnoses,
                    route=route, role=prior['role'], prompt=render_prompt, packet=packet,
                    task_characters=None, task_limit_characters=limit, forms=('bounded',))
        if selection['notice'] is None:
            raise ResearchError('review_input_too_large', notices.NOTICE_DELIVERY_MSG,
                                {'trials': selection['trials'], 'recovery': recovery})
    packet[KEY] = selection['notice']
    packet.pop('digest', None)
    packet['digest'] = digest(packet)
    prompt = selection['prompt']
    record = copy.deepcopy(prior)
    record.pop('capacity_replacement_of', None)
    for key in ('history_ref', 'history', 'review_parts_restart'):
        record.pop(key, None)
    record.update(value, packet=protocol._put_json(artifacts, packet), packet_digest=packet['digest'],
        prompt=prompt, prompt_hash=prompt['rendered_prompt_hash'], history=[],
        recorded_revision=revision)
    # Packet identity keeps the original spellings of these normalized identities.
    record.update({key: prior[key] for key in ('reviewer_id', 'author_id')})
    # The scientific parent stays the same. The replaced turn is counted separately for citations.
    record['slot_id'] = protocol.context_repair.slot_id(records, prior)
    if review_parts.find_kind(packet) is not None:
        record[review_parts.KEY] = copy.deepcopy(packet[review_parts.KEY])
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
    notice = packet.get(KEY, {})
    origin = assignment
    while 'capacity_replacement_of' in origin:
        parent = records.get('review_assignment', {}).get(origin['capacity_replacement_of'])
        require(parent is not None and parent['recorded_revision'] < origin['recorded_revision'])
        # Legacy refusals can carry a copied capacity marker. Only an identical
        # inherited notice keeps its parent's earlier boundary.
        if (parent.get('context', {}).get(KEY) is None
                or protocol._read_json(artifacts, parent['packet']).get(KEY) != notice):
            break
        require({key: value for key, value in read_position_context(origin).items() if key != 'reading_replacement'}
                == {key: value for key, value in read_position_context(parent).items() if key != 'reading_replacement'})
        origin = parent
    prior = records.get('review_assignment', {}).get(binding['assignment_id'])
    require(prior is not None and prior['recorded_revision'] < assignment['recorded_revision'])
    require(read_position_context(prior) == read_position_context(origin))
    require(all(prior.get(key) == assignment.get(key) for key in ('role', 'dossier_id', 'subject', 'route_id')))
    require(protocol.context_repair.slot_id(records, prior) == protocol.context_repair.slot_id(records, assignment))
    require(all(normalized_text(prior[key]) == normalized_text(assignment[key]) for key in ('reviewer_id', 'author_id')))
    entries = notice.get('refused', [])
    require(notice.get('protocol') == notices.PROTOCOL and notice.get('form') in ('whole', 'bounded')
            and isinstance(entries, list) and 1 <= len(entries) <= notices.REFUSED_ATTEMPT_LIMIT)
    position = set(list_turn_assignments(records, prior))
    certificates = sorted((certificate for certificate in records.get('review_output_refusal', {}).values()
        if certificate['assignment_id'] in position and certificate['recorded_revision'] < origin['recorded_revision']),
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
