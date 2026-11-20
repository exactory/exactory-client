"""Evidence-bound reassessment after actual reviewer-context contamination.

A repaired assignment occupies the original slot. Its predecessor remains
historical scientific evidence, including adverse findings and objections.
The link does not represent reused transport context or a new blind review.
"""

import json

from .errors import ResearchError, describe_error_at
from .evidence import digest
from .operations import fields, normalized_text, strings, text
from .review_supplied_evidence import collect_qualified_objections, find_scientific_predecessor
from .review_supplied_evidence import find_scientific_predecessor as _parent
from . import strategy

ROLES = ('bar', 'slate', 'result')
EXHAUSTION_PROTOCOL = 'exactory-review-context-repair-exhaustion-v1'
EXHAUSTION_MISMATCH_MSG = (
    'The member exhaustion of this context repair does not match the store. It must equal the binding that the plan '
    'of review-series gives for the exhausted head of a slate or result slot: the position, every refusal record '
    'of its completed outputs, and its correction. A first repair needs a position that is exhausted now. A repair '
    'that repeats the member of an earlier admitted repair of the same predecessor keeps that evidence. In both '
    'cases the position has no pending or accepted correction and no continuation. Take context.context_repair '
    'from the step of that plan.')
EXHAUSTION_ORIGIN_MSG = (
    'The stored records do not verify the exhaustion binding of this repair. The position, its completed outputs, '
    'its refusal records and its correction must be those that the store held before the repair. Take the step '
    'that review-series plans for the head.')
EXHAUSTION_PROBE_MSG = (
    'The named probe is missing, is not the current verified probe of the new route, or was not recorded after '
    'every bound refusal, correction and cause event. Run review-probe on the route, and name that probe in '
    'context.context_repair.probe_id.')
# The recorded events that end a reviewer context: a contamination of the context, or the retirement of its route.
CAUSE_KINDS = ('contamination', 'route_retired')
# The suffix of 0.56.0 and earlier, kept so that assignments recorded with it stay verified: it said "Return an
# additional reassessment object", which reviewers read as a second JSON object after the role's object.
PROMPT_0560 = (
    ' This is an explicit reassessment after recorded context contamination, not a new blind review. '
    'The historical scientific inputs, observed assessments and unresolved objections remain evidence. '
    'Do not treat their former independence as currently qualified. Return an additional reassessment '
    'object {assignment_id,findings:[{review_id,disposition:confirmed|revised|unresolved,reason,evidence}]}. '
    'Use the exact predecessor assignment_id from context_repair and cover every inherited final review '
    'of this phase. A source-request turn may defer reassessment; the final phase output must include it. '
    'A confirmed finding must retain its scientific support, value, objections and limitations. '
    'Explain any revised or unresolved finding with exact supplied evidence. A changed judgment alone '
    'does not dispose of a historical material objection; the ordinary objection procedure still applies.'
)
# The wording of the night of 2026-10-21, kept for the assignments recorded with it: a reviewer read "that object" as
# the value object and placed the reassessment inside it.
PROMPT_0561_NIGHT = (
    ' This is an explicit reassessment after recorded context contamination, not a new blind review. '
    'The historical scientific inputs, observed assessments and unresolved objections remain evidence. '
    'Do not treat their former independence as currently qualified. Your output is still the one JSON object of '
    'this role; give it one more member, "reassessment": {assignment_id,findings:[{review_id,disposition:confirmed|'
    'revised|unresolved,reason,evidence}]}, written inside that object before its closing brace, never as a second '
    'object or after the first object is closed. '
    'Use the exact predecessor assignment_id from context_repair and cover every inherited final review '
    'of this phase. A source-request turn may defer reassessment; the final phase output must include it. '
    'A confirmed finding must retain its scientific support, value, objections and limitations. '
    'Explain any revised or unresolved finding with exact supplied evidence. A changed judgment alone '
    'does not dispose of a historical material objection; the ordinary objection procedure still applies.'
)
# The wording before the exact-copy rule, kept for the assignments recorded with it: replacement reviewers read
# "retain" as keeping the meaning and labeled a reworded inherited review confirmed.
PROMPT_RETAIN_RULE = (
    ' This is an explicit reassessment after recorded context contamination, not a new blind review. '
    'The historical scientific inputs, observed assessments and unresolved objections remain evidence. '
    'Do not treat their former independence as currently qualified. Your output is still the one top-level JSON '
    'object of this role, with the top-level members stage, support, value, objections and limitations; add one more '
    'top-level member beside them, "reassessment": {assignment_id,findings:[{review_id,disposition:confirmed|revised|'
    'unresolved,reason,evidence}]}, at the same level as "value" (not inside "value", and not after the closing brace '
    'of the object). '
    'Use the exact predecessor assignment_id from context_repair and cover every inherited final review '
    'of this phase. A source-request turn may defer reassessment; the final phase output must include it. '
    'A confirmed finding must retain its scientific support, value, objections and limitations. '
    'Explain any revised or unresolved finding with exact supplied evidence. A changed judgment alone '
    'does not dispose of a historical material objection; the ordinary objection procedure still applies.'
)
# The exact-copy wording without the rule on prior objections, kept for the assignments recorded with it: a
# replacement reviewer restated inherited objections under their ids with reworded claims.
PROMPT_EXACT_COPY_RULE = (
    ' This is an explicit reassessment after recorded context contamination, not a new blind review. '
    'The historical scientific inputs, observed assessments and unresolved objections remain evidence. '
    'Do not treat their former independence as currently qualified. Your output is still the one top-level JSON '
    'object of this role, with the top-level members stage, support, value, objections and limitations; add one more '
    'top-level member beside them, "reassessment": {assignment_id,findings:[{review_id,disposition:confirmed|revised|'
    'unresolved,reason,evidence}]}, at the same level as "value" (not inside "value", and not after the closing brace '
    'of the object). '
    'Use the exact predecessor assignment_id from context_repair and cover every inherited final review '
    'of this phase. A source-request turn may defer reassessment; the final phase output must include it. '
    'Label an inherited review confirmed only when your output repeats its support, value, objections and limitations '
    'exactly; when your output changes any of them, including their wording, label it revised. '
    'Explain any revised or unresolved finding with exact supplied evidence. A changed judgment alone '
    'does not dispose of a historical material objection; the ordinary objection procedure still applies.'
)
# Every earlier suffix that a recorded assignment of the current version may carry.
EARLIER_PROMPTS = (PROMPT_0560, PROMPT_0561_NIGHT, PROMPT_RETAIN_RULE, PROMPT_EXACT_COPY_RULE)
PROMPT = (
    ' This is an explicit reassessment after recorded context contamination, not a new blind review. '
    'The historical scientific inputs, observed assessments and unresolved objections remain evidence. '
    'Do not treat their former independence as currently qualified. Your output is still the one top-level JSON '
    'object of this role, with the top-level members stage, support, value, objections and limitations; add one more '
    'top-level member beside them, "reassessment": {assignment_id,findings:[{review_id,disposition:confirmed|revised|'
    'unresolved,reason,evidence}]}, at the same level as "value" (not inside "value", and not after the closing brace '
    'of the object). '
    'Use the exact predecessor assignment_id from context_repair and cover every inherited final review '
    'of this phase. A source-request turn may defer reassessment; the final phase output must include it. '
    'Label an inherited review confirmed only when your output repeats its support, value, objections and limitations '
    'exactly; when your output changes any of them, including their wording, label it revised. '
    'A prior material objection receives its finding in objection_findings and is not restated in objections; '
    'objections lists only the material objections that you raise yourself, each under a new id. '
    'Explain any revised or unresolved finding with exact supplied evidence. A changed judgment alone '
    'does not dispose of a historical material objection; the ordinary objection procedure still applies.'
)
# The suffix of a repair with an exhaustion binding. It is recorded apart from the contamination suffixes, so
# every earlier prompt stays replayable. It states the cause of this repair and keeps the output rules of PROMPT.
EXHAUSTION_PROMPT_V1 = (
    ' This is an explicit reassessment, not a new blind review. Its cause is recorded in context_repair.exhaustion: '
    'when this reassessment was first admitted, the earlier position of this review slot held no usable answer, and '
    'the harness could admit no further replacement that carries a refusal, because the replacements were spent or '
    'no refusal notice fit. The binding names the assignments of that position and its recorded refusals, each the '
    'refusal of one answer by a mechanical check of the harness. It asserts no contamination of a reviewer context '
    'and no retirement of a route, and its cause events can be an empty list. '
    'The historical scientific inputs, the answers of that position and the unresolved objections remain '
    'evidence. The scientific history delivers those answers as raw observations. A refused answer is not a '
    'recorded review, and its refusal says nothing about the scientific judgment in it. '
    'Do not treat the former independence of these materials as currently qualified. Your output is still the one '
    'top-level JSON object of this role, with the top-level members stage, support, value, objections and '
    'limitations; add one more top-level member beside them, "reassessment": {assignment_id,findings:[{review_id,'
    'disposition:confirmed|revised|unresolved,reason,evidence}]}, at the same level as "value" (not inside "value", '
    'and not after the closing brace of the object). '
    'Use the exact predecessor assignment_id from context_repair and cover every inherited final review of this '
    'phase; when historical_reviews is empty, findings is an empty list. A source-request turn may defer '
    'reassessment; the final phase output must include it. '
    'Label an inherited review confirmed only when your output repeats its support, value, objections and limitations '
    'exactly; when your output changes any of them, including their wording, label it revised. '
    'A prior material objection receives its finding in objection_findings and is not restated in objections; '
    'objections lists only the material objections that you raise yourself, each under a new id. '
    'Explain any revised or unresolved finding with exact supplied evidence. A changed judgment alone '
    'does not dispose of a historical material objection; the ordinary objection procedure still applies.'
)


def is_same_route(first, second):
    """Compare the reviewer route identity of Ruling 2, excluding its id and runtime."""
    return all(first[key] == second[key] for key in ('adapter', 'model', 'endpoint', 'configuration'))


def build_exhaustion_binding(records, artifacts, assignment, *, before_revision=None):
    """Freeze the stored position without rechecking its scientific outputs."""
    from .review_failure_class import list_turn_assignments

    boundary = float('inf') if before_revision is None else before_revision
    position = sorted((records['review_assignment'][identifier]
                       for identifier in list_turn_assignments(records, assignment)
                       if records['review_assignment'][identifier]['recorded_revision'] < boundary),
                      key=lambda row: (row['recorded_revision'], row['id']))
    identifiers = {row['id'] for row in position}
    refusals = sorted((row for row in records.get('review_output_refusal', {}).values()
                       if row['assignment_id'] in identifiers and row['recorded_revision'] < boundary),
                      key=lambda row: (row['recorded_revision'], row['attempt_id']))
    corrections = sorted((row for row in records.get('review_output_correction_invocation', {}).values()
                          if row['assignment_id'] in identifiers and row['recorded_revision'] < boundary),
                         key=lambda row: (row['recorded_revision'], row['id']))
    correction = None
    if corrections:
        invocation = corrections[-1]
        observed = records.get('review_output_correction', {}).get(invocation['id'])
        status = (observed['correction_status'] if observed is not None
                  and observed['recorded_revision'] < boundary else 'pending')
        correction = dict(invocation_id=invocation['id'], status=status)
    return dict(protocol=EXHAUSTION_PROTOCOL, head_assignment_id=assignment['id'],
        position=[row['id'] for row in position],
        refusals=[dict(assignment_id=row['assignment_id'], attempt_id=row['attempt_id'],
            output_digest=row['output_digest'], refusal_digest=digest(row),
            recorded_revision=row['recorded_revision']) for row in refusals], correction=correction)


def _validate_exhaustion_records(records, artifacts, predecessor, exhaustion, *, before_revision=None):
    """Authenticate immutable exhaustion evidence, with no present-day output validator."""
    from .review_protocol import _read_json
    from .review_refusal_replacement import read_replacement_link

    def require(condition):
        if not condition:
            raise ResearchError('review_context_repair_unverified', EXHAUSTION_ORIGIN_MSG)

    expected = build_exhaustion_binding(records, artifacts, predecessor, before_revision=before_revision)
    require(exhaustion == expected and bool(expected['position']) and bool(expected['refusals']))
    require(expected['position'][-1] == predecessor['id'])
    boundary = float('inf') if before_revision is None else before_revision
    for index, identifier in enumerate(expected['position']):
        member = records['review_assignment'][identifier]
        require(member['recorded_revision'] < boundary)
        if index:
            require(read_replacement_link(member)[1] == expected['position'][index - 1])
    for entry in expected['refusals']:
        certificate = records.get('review_output_refusal', {}).get(entry['attempt_id'])
        attempt = records.get('review_attempt', {}).get(entry['attempt_id'])
        require(certificate is not None and attempt is not None and attempt['status'] == 'completed')
        require(attempt['assignment_id'] == entry['assignment_id']
                and attempt['recorded_revision'] < certificate['recorded_revision'] < boundary
                and attempt['request_digest'] == certificate['request_digest']
                and digest(_read_json(artifacts, attempt['output'])) == entry['output_digest'])
    correction = expected['correction']
    require(correction is None or correction['status'] not in ('pending', 'accepted'))
    return expected


def _find_exhaustion_boundary(records, exhaustion, events):
    revisions = [entry['recorded_revision'] for entry in exhaustion['refusals']]
    revisions.extend(event['recorded_revision'] for event in events)
    if exhaustion['correction'] is not None:
        identifier = exhaustion['correction']['invocation_id']
        revisions.append(records['review_output_correction_invocation'][identifier]['recorded_revision'])
        observed = records.get('review_output_correction', {}).get(identifier)
        if observed is not None:
            revisions.append(observed['recorded_revision'])
    return max(revisions)


def _assignment(records, identifier):
    text(identifier, 'Context predecessor assignment', code='invalid_review_context')
    saved = records.get('review_assignment', {}).get(identifier)
    if saved is None:
        raise ResearchError('review_context_repair_mismatch', 'The exact predecessor assignment must exist')
    return saved


def _lineage(records, assignment):
    chain, seen = [], set()
    while assignment is not None:
        identifier = assignment['id']
        if identifier in seen:
            raise ResearchError('review_context_repair_mismatch', 'Reviewer lineage must be acyclic')
        seen.add(identifier)
        chain.append(assignment)
        parent = find_scientific_predecessor(assignment)
        assignment = _assignment(records, parent) if parent is not None else None
    return list(reversed(chain))


def slot_id(records, assignment):
    """Source continuations and context repairs preserve one initial slot."""
    original = _lineage(records, assignment)[0]
    return original.get('slot_id', original['id'])


def is_same_subject(first, second):
    """Keep problem result slots tied to the exact statement, and bars to the field."""
    left, right = first.get('subject'), second.get('subject')
    if first.get('role') == 'bar' and left is not None and right is not None:
        return left.get('kind') == right.get('kind') == 'problem'
    return left == right


def require_current_slot(records, assignment):
    """A replaced failed invocation cannot later reopen its former slot."""
    if (assignment['role'] not in ROLES
            and not (assignment['role'] == 'adjudicator'
                     and assignment.get('review_parts', {}).get('adapter') == 'context_repair')):
        return
    current_slot = slot_id(records, assignment)
    for later in records.get('review_assignment', {}).values():
        if (later['role'] == assignment['role'] and later['dossier_id'] == assignment['dossier_id']
                and is_same_subject(later, assignment)
                and later['recorded_revision'] > assignment['recorded_revision']
                and slot_id(records, later) == current_slot):
            raise ResearchError('review_assignment_superseded', 'Continue the current assignment in this fixed slot; a replaced invocation cannot be retried')


def _exposure_lineage(records, assignment):
    """Actual prior scientific context can cross phases without merging slots."""
    ordered, visited, active = [], set(), set()

    def visit(current):
        identifier = current['id']
        if identifier in active:
            raise ResearchError('review_context_repair_mismatch', 'Reviewer context ancestry must be acyclic')
        if identifier in visited:
            return
        active.add(identifier)
        parents = [find_scientific_predecessor(current), current.get('history_assignment_id'),
                   current.get('context', {}).get('output_refusal', {}).get('assignment_id')]
        for parent in dict.fromkeys(value for value in parents if value is not None):
            visit(_assignment(records, parent))
        active.remove(identifier)
        visited.add(identifier)
        ordered.append(current)
        if current.get('staged_review', {}).get('kind') == 'controller':
            for child in sorted(records.get('review_assignment', {}).values(),
                                key=lambda row: (row.get('recorded_revision', 0), row['id'])):
                if child.get('staged_review', {}).get('controller_assignment_id') == identifier:
                    visit(child)

    visit(assignment)
    return ordered


def cause_events(records, identifiers):
    """The recorded contaminations and route retirements of the assignments in a lineage, in recorded order."""
    return sorted((event for event in records.get('review_context_event', {}).values()
                   if event['assignment_id'] in identifiers and event['kind'] in CAUSE_KINDS),
                  key=lambda event: (event['recorded_revision'], event['id']))


def _reviews(records, identifiers):
    return sorted((saved for saved in list(records.get('value_review', {}).values())
                   + list(records.get('review_adjudication', {}).values())
                   if saved['assignment_id'] in identifiers),
                  key=lambda value: (value.get('recorded_revision', 0), value['id']))


def historical_reviews(records, dossier_id, stage):
    """Return retained findings whose independence was lost or reassessed."""
    historical = set()
    contaminated = {event['assignment_id'] for event in records.get('review_context_event', {}).values()
                    if event['kind'] == 'contamination'}
    for saved in records.get('review_assignment', {}).values():
        if saved['dossier_id'] != dossier_id or saved['role'] != stage:
            continue
        chain = _lineage(records, saved)
        for index, ancestor in enumerate(chain):
            if ancestor.get('context', {}).get('context_repair') is not None:
                historical.update(item['id'] for item in chain[:index])
        exposure = _exposure_lineage(records, saved)
        if any(ancestor['id'] in contaminated for ancestor in exposure):
            # A repair starts a new observed context. Contamination earlier in
            # that lineage does not contaminate its fresh transport by itself.
            last_repair = max((i for i, item in enumerate(exposure)
                               if item.get('context', {}).get('context_repair') is not None), default=0)
            if any(item['id'] in contaminated for item in exposure[last_repair:]):
                historical.add(saved['id'])
    return _reviews(records, historical)


def _raise_binding_mismatch(repair, events):
    """Name the full recorded binding without changing the legacy set-based acceptance rule."""
    expected_ids = [event['id'] for event in events]
    missing_ids = [identifier for identifier in expected_ids if identifier not in repair['event_ids']]
    expected_evidence = [item for event in events for item in event['evidence']]
    empty_events_message = (
        'When the lineage records no such event, a repair with the member exhaustion sets event_ids and evidence to '
        'empty lists.'
        if 'exhaustion' in repair else
        'If the list of events is empty, the lineage records no contamination and no route retirement, and a '
        'context repair needs one.')
    raise ResearchError('review_context_repair_mismatch',
        'The context repair does not name exactly the events and the evidence that the lineage of its predecessor '
        'records. A context repair binds every contamination and route retirement event of that lineage, with the '
        'evidence recorded for each event. '
        f'The lineage records these events, in the order of their recording: {json.dumps(expected_ids)}. '
        f'Of these, event_ids does not name: {json.dumps(missing_ids)}. '
        f'The evidence of these events, in the same order, is: {json.dumps(expected_evidence, sort_keys=True)}. '
        'Set event_ids to exactly these events and evidence to exactly this evidence. '
        + empty_events_message,
        {'expected_event_ids': expected_ids, 'missing_event_ids': missing_ids, 'expected_evidence': expected_evidence})


def find_chain_head(records, assignment):
    """Follow the actual later continuation and replacement of a repair."""
    descendants = {assignment['id']}
    head = assignment
    for later in sorted(records.get('review_assignment', {}).values(),
                        key=lambda row: (row['recorded_revision'], row['id'])):
        context = later.get('context', {})
        if (_parent(later) in descendants
                or context.get('output_refusal', {}).get('assignment_id') in descendants):
            descendants.add(later['id'])
            head = later
    return head


def _has_recorded_exhaustion_repair(records, artifacts, predecessor, exhaustion):
    """A repair or its replacement retains the admitted proof without rechecking outputs."""
    for prior in records.get('review_assignment', {}).values():
        binding = prior.get('context', {}).get('context_repair', {})
        if (binding.get('assignment_id') != predecessor['id']
                or binding.get('exhaustion') != exhaustion):
            continue
        try:
            validate_repair_binding(records, artifacts, prior)
        except ResearchError as error:
            if error.code == 'review_context_repair_stale':
                return True
        else:
            return True
    return False


def prepare_repair(records, artifacts, assignment_payload):
    """Validate a prospective repair without altering the original record."""
    from .review_protocol import assignment_state, route_state

    context = assignment_payload['context']
    fields(context, (), ('context_repair', 'prior_assignment_id', 'source_links', 'source_delivery'), code='invalid_review_context')
    if 'context_repair' not in context:
        return None
    fields(context, ('context_repair',), code='invalid_review_context')
    repair = context['context_repair']
    fields(repair, ('assignment_id', 'event_ids', 'probe_id', 'reason', 'evidence'),
           ('exhaustion',), code='invalid_review_context')
    text(repair['reason'], 'Context repair reason', code='invalid_review_context')
    strings(repair['event_ids'], 'Exact contamination event IDs')
    text(repair['probe_id'], 'Current context probe', code='invalid_review_context')
    predecessor = _assignment(records, repair['assignment_id'])
    exhaustion = repair.get('exhaustion')
    if 'exhaustion' in repair and not isinstance(exhaustion, dict):
        raise ResearchError('review_context_repair_mismatch', EXHAUSTION_MISMATCH_MSG)
    if exhaustion is not None:
        if (assignment_payload['role'] not in ('slate', 'result')
                or exhaustion != build_exhaustion_binding(records, artifacts, predecessor)
                or (exhaustion['correction'] is not None
                    and exhaustion['correction']['status'] in ('pending', 'accepted'))
                or any(later.get('context', {}).get('prior_assignment_id') == predecessor['id']
                       for later in records.get('review_assignment', {}).values())):
            raise ResearchError('review_context_repair_mismatch', EXHAUSTION_MISMATCH_MSG)
        _validate_exhaustion_records(records, artifacts, predecessor, exhaustion)
        from .review_protocol import has_exhausted_position
        recorded_exhaustion = _has_recorded_exhaustion_repair(records, artifacts, predecessor, exhaustion)
        if not recorded_exhaustion and not has_exhausted_position(records, artifacts, predecessor):
            raise ResearchError('review_context_repair_mismatch', EXHAUSTION_MISMATCH_MSG)
    roles = ROLES + (('adjudicator',) if predecessor.get('staged_review') is not None
                     or predecessor.get('review_parts') is not None else ())
    if (assignment_payload['role'] not in roles or predecessor['role'] != assignment_payload['role']
            or predecessor['dossier_id'] != assignment_payload['dossier_id']
            or not is_same_subject(predecessor, assignment_payload)
            or normalized_text(predecessor['author_id']) != normalized_text(assignment_payload['author_id'])):
        raise ResearchError('review_context_repair_mismatch', 'Reassess the exact phase, dossier and author boundary')
    chain = _exposure_lineage(records, predecessor)
    identifiers = {item['id'] for item in chain}
    epochs = {item.get('staged_review', {}).get('epoch_id') for item in chain} - {None}
    identifiers.update(item['id'] for item in records.get('review_assignment', {}).values()
                       if item.get('staged_review', {}).get('epoch_id') in epochs)
    if (any(item['assignment_id'] in identifiers and item['id'] not in records.get('review_attempt', {})
            for item in records.get('review_invocation', {}).values())
            or any(item.get('epoch_id') in epochs and item.get('state') not in ('released', 'terminated')
                   and not item.get('released') for item in records.get('review_reservation', {}).values())):
        raise ResearchError('review_context_repair_pending', 'Reconcile pending reviewer workers and invocations before repairing their context.')
    events = cause_events(records, identifiers)
    if ((not events and exhaustion is None)
            or set(repair['event_ids']) != {event['id'] for event in events}):
        _raise_binding_mismatch(repair, events)
    strategy.evidence(records, artifacts, repair['evidence'], required=exhaustion is None or bool(events))
    if exhaustion is not None and (repair['event_ids'] != [event['id'] for event in events]
            or repair['evidence'] != [item for event in events for item in event['evidence']]):
        _raise_binding_mismatch(repair, events)
    if {digest(item) for item in repair['evidence']} != {digest(item) for event in events for item in event['evidence']}:
        _raise_binding_mismatch(repair, events)
    from .review_protocol import is_dead_assignment, retired_routes
    retired = retired_routes(records)
    state = assignment_state(records, artifacts, predecessor['id'])
    dependency_codes = {'review_source_parent_unverified', 'review_context_repair_stale'}
    dependency_lost = any(item['code'] in dependency_codes for item in state['obligations'])
    if (exhaustion is None and state['context_status'] != 'contaminated' and not any(event['kind'] == 'contamination' for event in events) and not dependency_lost
            and predecessor['route_id'] not in retired):
        raise ResearchError('review_context_repair_mismatch', 'Repair an actual contaminated or retired reviewer context')
    proof = route_state(records, artifacts, assignment_payload['route_id'])
    probe = records.get('review_route_probe', {}).get(repair['probe_id'])
    last_bound_revision = (_find_exhaustion_boundary(records, exhaustion, events) if exhaustion is not None
                           else max(event['recorded_revision'] for event in events))
    if (proof['context_status'] != 'verified_for_route' or proof['probe_id'] != repair['probe_id']
            or probe is None or probe['recorded_revision'] <= last_bound_revision):
        raise ResearchError('review_context_repair_unverified', EXHAUSTION_PROBE_MSG if exhaustion is not None
                            else 'A current observed route probe after contamination must precede reassessment')
    original_slot = slot_id(records, predecessor)
    reviewer = normalized_text(assignment_payload['reviewer_id'])
    for prior in records.get('review_assignment', {}).values():
        if (prior['dossier_id'] != predecessor['dossier_id'] or prior['role'] != predecessor['role']
                or not is_same_subject(prior, predecessor)):
            continue
        if (slot_id(records, prior) != original_slot and normalized_text(prior['reviewer_id']) == reviewer
                and not is_dead_assignment(records, prior)):
            raise ResearchError('review_not_independent', 'The other fixed slot cannot reassess this slot')
        if prior['id'] not in identifiers and predecessor['id'] in {item['id'] for item in _lineage(records, prior)}:
            head = find_chain_head(records, prior)
            attempts = [item for item in records.get('review_attempt', {}).values() if item['assignment_id'] == head['id']]
            pending = any(item['assignment_id'] == head['id'] and item['id'] not in records.get('review_attempt', {})
                          for item in records.get('review_invocation', {}).values())
            dead = is_dead_assignment(records, head)
            from .review_parts_repair import has_exhausted_over_cap_repair
            from .review_protocol import has_exhausted_position
            exhausted = (has_exhausted_over_cap_repair(records, artifacts, head)
                         or has_exhausted_position(records, artifacts, head))
            stale_exhaustion = False
            prior_binding = prior.get('context', {}).get('context_repair', {})
            if exhaustion is not None and prior_binding.get('exhaustion') == exhaustion:
                try:
                    validate_repair_binding(records, artifacts, head)
                except ResearchError as error:
                    stale_exhaustion = error.code == 'review_context_repair_stale'
            if (pending or (not attempts or any(item['status'] not in ('failed', 'incomplete') for item in attempts))
                    and not dead and not exhausted and not stale_exhaustion):
                raise ResearchError('review_resampling_forbidden', 'Retain the existing repair or source continuation without forking')
    history = collect_scientific_history(records, artifacts, chain, roles,
        exhausted_position=exhaustion['position'] if exhaustion is not None else ())
    predecessor_history = next(item for item in history if item['assignment_id'] == predecessor['id'])
    result = {'predecessor': predecessor, 'slot_id': original_slot, 'packet': predecessor_history['packet'],
            'predecessor_binding': bind_predecessor(records, predecessor, state, history),
            'historical_reviews': _reviews(records, {item['id'] for item in chain if item['role'] == predecessor['role']}),
            'scientific_history': history,
            'event_ids': ([event['id'] for event in events] if exhaustion is not None
                          else sorted(repair['event_ids'])), 'probe_id': repair['probe_id'],
            'cause': [{key: event[key] for key in ('id', 'kind', 'source', 'reason')} for event in events]}
    if exhaustion is not None:
        result['exhaustion'] = exhaustion
    return result


def collect_scientific_history(records, artifacts, chain, roles, *, exhausted_position=()):
    """Read authenticated scientific observations for a repair or its source continuation."""
    from .review_protocol import assignment_state, source_delivery_packet

    history = []
    for ancestor in chain:
        if ancestor.get('staged_review', {}).get('kind') == 'task':
            from .review_staged_protocol import build_state
            prior_state = build_state(records, artifacts, ancestor, include_contaminated=True)
        else:
            prior_state = assignment_state(records, artifacts, ancestor['id'])
        graph = None
        if ancestor.get('staged_review', {}).get('kind') == 'controller':
            from .direction_reviews import collect_historical_task_graph
            try:
                graph = collect_historical_task_graph(records, artifacts, ancestor['id'])
            except ResearchError as error:
                if error.code != 'review_invocation_unverified':
                    raise
                raise ResearchError('review_context_repair_unverified',
                    'Preserve the original instrumented observation provenance.', error.details) from error
            prior_state = dict(prior_state, effective=graph['effective'], obligations=graph['obligations'])
        from .research_decisions import read_effective_result
        scientific_output, _ = read_effective_result(prior_state)
        # A frozen exhaustion successor keeps these refusals as raw history,
        # including when a later validator would accept one of their outputs.
        if ancestor['id'] in exhausted_position:
            scientific_output = None
        packet = source_delivery_packet(records, artifacts, ancestor)
        from . import review_problem_parts, review_parts
        intermediate_output = None
        if (review_problem_parts.find_stage(packet) == 'part'
                or review_parts.find_kind(packet) is not None
                and (review_parts.find_kind(packet) != 'final'
                     or (scientific_output or {}).get('kind') == 'requests')):
            intermediate_output, scientific_output = scientific_output, None
        if ancestor.get('staged_review', {}).get('kind') == 'task':
            intermediate_output, scientific_output = prior_state.get('raw_output'), None
        # Selection of a later head does not invalidate the exact earlier scientific history.
        allowed = {'review_context_contaminated', 'review_source_parent_unverified', 'review_context_repair_stale',
                   'review_route_retired', 'review_assignment_replaced', 'review_assignment_superseded'}
        if scientific_output is None:
            allowed.update(('review_output_pending', 'review_output_refused', 'review_task_pending',
                            'assessment_incomplete', 'review_source_coverage_pending', 'review_composition_pending'))
        if any(item['code'] not in allowed for item in prior_state['obligations']):
            raise ResearchError('review_context_repair_unverified', 'Preserve only the exact observed scientific history with verified invocation provenance')
        from .bar_sources import pending
        epoch = records.get('review_epoch', {}).get(ancestor.get('staged_review', {}).get('epoch_id'), {})
        if (scientific_output is not None and ancestor['role'] in roles
                and not pending(packet, scientific_output, manifest=epoch.get('manifest'))
                and not scientific_output.get('settled_requests')):
            finals = _reviews(records, {ancestor['id']})
            if not any({key: value for key, value in saved.get('payload', saved).items()
                        if key not in ('id', 'dossier_id', 'assignment_id')} == scientific_output for saved in finals):
                # Adjudication records also retain protocol and source bindings.
                if ancestor['role'] == 'adjudicator' and any(
                        _scientific_content(saved) == _scientific_content(scientific_output)
                        and saved.get('reassessment') == scientific_output.get('reassessment')
                        for saved in finals):
                    pass
                else:
                    raise ResearchError('review_context_repair_history_unrecorded',
                        'Record the exact observed final as historical scientific content before context reassessment',
                        {'assignment_id': ancestor['id']})
        item = {'assignment_id': ancestor['id'], 'packet': packet,
                'observed_output': scientific_output, 'independence': 'no_longer_qualified'}
        if intermediate_output is not None:
            item['intermediate_output'] = intermediate_output
        if ancestor.get('staged_review', {}).get('kind') == 'controller' and prior_state.get('effective') is not None:
            from .research_decisions import bind_effective_result
            item['effective_binding'] = bind_effective_result(prior_state['effective'])
        if graph is not None:
            item['selected_observations'] = graph['selection']['observations']
            item['intermediate_findings'] = [finding for observation in graph['selection']['observations']
                                             for finding in observation['output'].get('findings', [])]
        from . import review_protocol as protocol
        if (ancestor.get('staged_review') is not None or scientific_output is None) and hasattr(protocol, 'read_raw_observation'):
            observed = []
            for attempt in records.get('review_attempt', {}).values():
                if attempt['assignment_id'] != ancestor['id'] or not attempt.get('output'):
                    continue
                if ancestor.get('staged_review') is not None:
                    from .review_staged_protocol import authenticate_capture_provenance
                    authenticated = authenticate_capture_provenance(records, artifacts, ancestor, attempt)
                else:
                    authenticated = protocol.authenticate_provenance(records, artifacts, ancestor, attempt)
                if not authenticated:
                    raise ResearchError('review_context_repair_unverified', 'Preserve the original instrumented observation provenance.')
                observed.append(protocol.read_raw_observation(records, artifacts, attempt['id']))
            if observed:
                item['raw_observations'] = observed
        history.append(item)
    return history


def bind_predecessor(records, predecessor, state, history):
    attempts = sorted((item for item in records.get('review_attempt', {}).values()
                       if item['assignment_id'] == predecessor['id']),
                      key=lambda item: (item.get('recorded_revision', 0), item['id']))
    prior = next(item for item in history if item['assignment_id'] == predecessor['id'])
    return dict(assignment_id=predecessor['id'], attempt_ids=[item['id'] for item in attempts],
        assignment_digest=digest(predecessor),
        attempt_digests={item['id']: digest(item) for item in attempts},
        no_output=not (state.get('raw_output') is not None or prior['observed_output'] is not None
                       or prior.get('intermediate_output') is not None or prior.get('raw_observations')),
        no_effective_output=prior['observed_output'] is None,
        selected_attempt_id=state.get('attempt_id'), raw_output_digest=state.get('raw_output_digest'),
        last_observed_assignment_id=next((item['assignment_id'] for item in reversed(history)
            if item['observed_output'] is not None or item.get('intermediate_output') is not None
            or item.get('raw_observations')), None))


def _scientific_content(output):
    if 'objection_id' in output:
        return {key: output.get(key) for key in ('objection_id', 'disposition', 'reason', 'evidence', 'correction_admissible')}
    content = {key: output.get(key) for key in ('stage', 'support', 'value', 'objections', 'limitations')}
    if isinstance(content['support'], dict):
        content['support'] = {key: value for key, value in content['support'].items() if key not in ('id', 'assessor')}
    return content


def _is_unchanged(original, output, identities):
    """Whether the output repeats the original's scientific content, each objection compared under its identity.

    identities maps the stated id and claim of each qualified objection that the frozen repair packet delivers to its
    identity; an output objection with that id and claim is compared under the identity."""
    if 'objection_id' in original or 'objection_id' in output:
        return _scientific_content(original) == _scientific_content(output)
    original_objections = [{key: value for key, value in objection.items() if key != 'stated_id'}
                           for objection in original['objections']]
    output_objections = [dict(objection, id=identities.get((objection['id'], objection['claim']), objection['id']))
                         for objection in output['objections']]
    return (_scientific_content(dict(original, objections=original_objections))
            == _scientific_content(dict(output, objections=output_objections)))


def reassessment_reviews(records, assignment):
    """Find all same-phase final findings inherited by the latest repair."""
    repairs = [item for item in _lineage(records, assignment)
               if item.get('context', {}).get('context_repair') is not None]
    if not repairs:
        return []
    predecessor = _assignment(records, repairs[-1]['context']['context_repair']['assignment_id'])
    return _reviews(records, {item['id'] for item in _lineage(records, predecessor)})


def collect_problem_review_history(records, assignment):
    """Retain a problem reviewer's earlier final findings of the same phase across refreshes and continuations."""
    if (assignment.get('subject', {}).get('kind') != 'problem'
            or assignment['role'] not in ROLES + ('adjudicator',)):
        return []
    return _reviews(records, {item['id'] for item in _lineage(records, assignment)
                              if item['role'] == assignment['role']})


def validate_repair_origin(records, artifacts, assignment):
    """Retain exact original provenance when recording historical science.

    Current independent approval additionally requires validate_repair_binding.
    Later exposure does not erase an already observed scientific response.
    """
    from .review_protocol import route_state

    repairs = [item for item in _lineage(records, assignment)
               if item.get('context', {}).get('context_repair') is not None]
    if not repairs:
        return
    bound = repairs[-1]
    repair = bound['context']['context_repair']
    predecessor = _assignment(records, repair['assignment_id'])
    identifiers = {item['id'] for item in _exposure_lineage(records, predecessor)}
    events = [event for event in cause_events(records, identifiers)
              if event['recorded_revision'] < bound['recorded_revision']]
    if set(repair['event_ids']) != {event['id'] for event in events}:
        raise ResearchError('review_context_repair_unverified', 'The original repair must cover its complete prospective exposure history')
    exhaustion = repair.get('exhaustion')
    if 'exhaustion' in repair and not isinstance(exhaustion, dict):
        raise ResearchError('review_context_repair_unverified', EXHAUSTION_ORIGIN_MSG)
    if exhaustion is not None:
        if (bound['role'] not in ('slate', 'result')
                or repair['event_ids'] != [event['id'] for event in events]
                or repair['evidence'] != [item for event in events for item in event['evidence']]):
            raise ResearchError('review_context_repair_unverified', EXHAUSTION_ORIGIN_MSG)
        _validate_exhaustion_records(records, artifacts, predecessor, exhaustion,
                                     before_revision=bound['recorded_revision'])
        last_bound_revision = _find_exhaustion_boundary(records, exhaustion, events)
    else:
        last_bound_revision = max((event['recorded_revision'] for event in events), default=0)
    probe = records.get('review_route_probe', {}).get(repair['probe_id'])
    if ((not events and exhaustion is None) or probe is None or probe['route_id'] != bound['route_id']
            or probe['recorded_revision'] <= last_bound_revision
            or probe['recorded_revision'] >= bound.get('recorded_revision', float('inf'))):
        raise ResearchError('review_context_repair_unverified', 'The repair must retain its actual prospective context probe')
    exact_probe_records = dict(records, review_route_probe={probe['id']: probe})
    proof = route_state(exact_probe_records, artifacts, bound['route_id'])
    if proof['context_status'] != 'verified_for_route' or proof['probe_id'] != probe['id']:
        raise ResearchError('review_context_repair_unverified', 'The exact prospective probe bytes and observed response must remain verified')
    for event in events:
        strategy.evidence(records, artifacts, event['evidence'])


def validate_repair_binding(records, artifacts, assignment):
    """Current approval also requires coverage of later ancestor exposure."""
    validate_repair_origin(records, artifacts, assignment)
    repairs = [item for item in _lineage(records, assignment)
               if item.get('context', {}).get('context_repair') is not None]
    if not repairs:
        return
    repair = repairs[-1]['context']['context_repair']
    predecessor = _assignment(records, repair['assignment_id'])
    identifiers = {item['id'] for item in _exposure_lineage(records, predecessor)}
    events = {event['id'] for event in cause_events(records, identifiers)}
    if set(repair['event_ids']) != events:
        raise ResearchError('review_context_repair_stale', 'Later predecessor contamination requires explicit reassessment of the new evidence')


def frozen_reassessment_packet(records, artifacts, assignment):
    """The actual latest repair fixes its predecessor finals, even across later continuations/refreshes."""
    repairs = [item for item in _lineage(records, assignment)
               if item.get('context', {}).get('context_repair') is not None]
    bound = repairs[-1] if repairs else assignment
    return json.loads(artifacts.read(bound['packet']))


def validate_reassessment(records, artifacts, assignment, observed_output, *, reading_checks=True, packet=None, evidence_validator=None):
    """Require an observed, evidenced disposition of every inherited finding."""
    if packet is None:
        chain = _lineage(records, assignment)
        repairs = [item for item in chain if item.get('context', {}).get('context_repair') is not None]
        repair = repairs[-1]['context']['context_repair'] if repairs else None
        inherited = reassessment_reviews(records, assignment) if repairs else []
    else:
        repair = packet.get('context_repair')
        inherited = packet.get('historical_reviews', []) if repair else []
    if repair is None:
        if 'reassessment' in observed_output:
            raise ResearchError('review_reassessment_mismatch', 'An ordinary review has no reassessment predecessor')
        return
    from . import review_parts
    delivery = None
    if assignment.get(review_parts.KEY) is not None:
        frozen = json.loads(artifacts.read(assignment['packet']))
        logical = review_parts.read_logical_packet(artifacts, frozen[review_parts.KEY])
        repair = logical.get('context_repair', repair)
        inherited = logical.get('historical_reviews', inherited)
        packet = logical
        packets = review_parts.collect_contract_packets(records, artifacts, assignment)
        delivery = review_parts.read_series_delivery(records, artifacts, packets=packets)
    predecessor_id = repair['assignment_id']
    if 'reassessment' not in observed_output:
        raise ResearchError('review_reassessment_missing', 'The observed output must address the retained historical findings')
    reassessment = observed_output['reassessment']
    fields(reassessment, ('assignment_id', 'findings'), code='review_reassessment_mismatch')
    if reassessment['assignment_id'] != predecessor_id:
        raise ResearchError('review_reassessment_mismatch', 'Reassess the exact recorded predecessor')
    findings = strategy.items(reassessment['findings'], 'Reassessment findings')
    seen = set()
    originals = {saved['id']: saved.get('payload', saved) for saved in inherited}
    # Read wherever the harness delivered a qualified objection in the frozen packet, and never in a reviewer's
    # observed output: an inherited review recorded under a qualified identity itself delivers no stated_id.
    identities = {(objection['stated_id'], objection['claim']): objection['id']
                  for objection in collect_qualified_objections(packet)}
    for index, finding in enumerate(findings):
        fields(finding, ('review_id', 'disposition', 'reason', 'evidence'), code='review_reassessment_mismatch')
        text(finding['review_id'], 'Historical review ID', code='review_reassessment_mismatch')
        if finding['review_id'] in seen or finding['review_id'] not in originals:
            raise ResearchError('review_reassessment_incomplete', 'Address each inherited final review exactly once')
        seen.add(finding['review_id'])
        strategy.choice(finding['disposition'], ('confirmed', 'revised', 'unresolved'), 'Reassessment disposition', 'review_reassessment_mismatch')
        text(finding['reason'], 'Reassessment scientific reasoning', code='review_reassessment_mismatch')
        binding = assignment.get('subject', {}).get('kind') == 'problem'
        if reading_checks:
            strategy.evidence(records, artifacts, finding['evidence'], review_binding=binding)
        else:
            from .review_refusal import validate_evidence_contract
            with describe_error_at('/reassessment/findings/' + str(index) + '/evidence'):
                (evidence_validator or validate_evidence_contract)(records, artifacts, finding['evidence'],
                                                                   review_binding=binding)
        if delivery is not None:
            from .research_decisions import find_review_undelivered_references
            missing = find_review_undelivered_references(records, artifacts, finding['evidence'], delivery)
            if missing:
                raise ResearchError('review_evidence_undelivered',
                                    'Reassessment evidence must have reached this series', missing[0])
        from .review_protocol import _reference_templates
        content = _reference_templates(observed_output) if packet is not None else observed_output
        if finding['disposition'] == 'confirmed' and not _is_unchanged(originals[finding['review_id']], content, identities):
            raise ResearchError('review_reassessment_mismatch', 'A changed scientific finding cannot be labeled confirmed')
    if seen != set(originals):
        raise ResearchError('review_reassessment_incomplete', 'Address every inherited final review')
