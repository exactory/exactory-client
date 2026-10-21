"""Evidence-bound reassessment after actual reviewer-context contamination.

A repaired assignment occupies the original slot. Its predecessor remains
historical scientific evidence, including adverse findings and objections.
The link does not represent reused transport context or a new blind review.
"""

import json

from .errors import ResearchError
from .evidence import digest
from .operations import fields, normalized_text, strings, text
from . import strategy

ROLES = ('bar', 'slate', 'result')
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
PROMPT = (
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


def _assignment(records, identifier):
    text(identifier, 'Context predecessor assignment', code='invalid_review_context')
    saved = records.get('review_assignment', {}).get(identifier)
    if saved is None:
        raise ResearchError('review_context_repair_mismatch', 'The exact predecessor assignment must exist')
    return saved


def _parent(assignment):
    context = assignment.get('context', {})
    repair = context.get('context_repair')
    return repair.get('assignment_id') if isinstance(repair, dict) else context.get('prior_assignment_id')


def _lineage(records, assignment):
    chain, seen = [], set()
    while assignment is not None:
        identifier = assignment['id']
        if identifier in seen:
            raise ResearchError('review_context_repair_mismatch', 'Reviewer lineage must be acyclic')
        seen.add(identifier)
        chain.append(assignment)
        parent = _parent(assignment)
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
    if assignment['role'] not in ROLES:
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
        parents = [_parent(current), current.get('history_assignment_id')]
        for parent in dict.fromkeys(value for value in parents if value is not None):
            visit(_assignment(records, parent))
        active.remove(identifier)
        visited.add(identifier)
        ordered.append(current)

    visit(assignment)
    return ordered


def cause_events(records, identifiers):
    """The recorded contaminations and route retirements of the assignments in a lineage, in recorded order."""
    return sorted((event for event in records.get('review_context_event', {}).values()
                   if event['assignment_id'] in identifiers and event['kind'] in CAUSE_KINDS),
                  key=lambda event: (event['recorded_revision'], event['id']))


def _reviews(records, identifiers):
    return sorted((saved for saved in records.get('value_review', {}).values()
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


def prepare_repair(records, artifacts, assignment_payload):
    """Validate a prospective repair without altering the original record."""
    from .review_protocol import assignment_state, route_state

    context = assignment_payload['context']
    fields(context, (), ('context_repair', 'prior_assignment_id', 'source_links'), code='invalid_review_context')
    if 'context_repair' not in context:
        return None
    fields(context, ('context_repair',), code='invalid_review_context')
    repair = context['context_repair']
    fields(repair, ('assignment_id', 'event_ids', 'probe_id', 'reason', 'evidence'), code='invalid_review_context')
    text(repair['reason'], 'Context repair reason', code='invalid_review_context')
    strings(repair['event_ids'], 'Exact contamination event IDs')
    text(repair['probe_id'], 'Current context probe', code='invalid_review_context')
    predecessor = _assignment(records, repair['assignment_id'])
    if (assignment_payload['role'] not in ROLES or predecessor['role'] != assignment_payload['role']
            or predecessor['dossier_id'] != assignment_payload['dossier_id']
            or not is_same_subject(predecessor, assignment_payload)
            or normalized_text(predecessor['author_id']) != normalized_text(assignment_payload['author_id'])):
        raise ResearchError('review_context_repair_mismatch', 'Reassess the exact phase, dossier and author boundary')
    chain = _exposure_lineage(records, predecessor)
    identifiers = {item['id'] for item in chain}
    events = cause_events(records, identifiers)
    if not events or set(repair['event_ids']) != {event['id'] for event in events}:
        raise ResearchError('review_context_repair_mismatch', 'Bind every actual contamination or retirement event in the predecessor lineage')
    strategy.evidence(records, artifacts, repair['evidence'])
    if {digest(item) for item in repair['evidence']} != {digest(item) for event in events for item in event['evidence']}:
        raise ResearchError('review_context_repair_mismatch', 'Use the exact recorded contamination evidence without author substitutes')
    from .review_protocol import retired_routes
    retired = retired_routes(records)
    state = assignment_state(records, artifacts, predecessor['id'])
    dependency_codes = {'review_source_parent_unverified', 'review_context_repair_stale'}
    dependency_lost = any(item['code'] in dependency_codes for item in state['obligations'])
    if (state['context_status'] != 'contaminated' and not dependency_lost
            and predecessor['route_id'] not in retired) or state['output'] is None:
        raise ResearchError('review_context_repair_mismatch', 'Repair an actual contaminated or retired observed assessment')
    proof = route_state(records, artifacts, assignment_payload['route_id'])
    probe = records.get('review_route_probe', {}).get(repair['probe_id'])
    if (proof['context_status'] != 'verified_for_route' or proof['probe_id'] != repair['probe_id']
            or probe is None or probe['recorded_revision'] <= max(event['recorded_revision'] for event in events)):
        raise ResearchError('review_context_repair_unverified', 'A current observed route probe after contamination must precede reassessment')
    original_slot = slot_id(records, predecessor)
    reviewer = normalized_text(assignment_payload['reviewer_id'])
    for prior in records.get('review_assignment', {}).values():
        if (prior['dossier_id'] != predecessor['dossier_id'] or prior['role'] != predecessor['role']
                or not is_same_subject(prior, predecessor)):
            continue
        if slot_id(records, prior) != original_slot and normalized_text(prior['reviewer_id']) == reviewer:
            raise ResearchError('review_not_independent', 'The other fixed slot cannot reassess this slot')
        if prior['id'] not in identifiers and predecessor['id'] in {item['id'] for item in _lineage(records, prior)}:
            attempts = [item for item in records.get('review_attempt', {}).values() if item['assignment_id'] == prior['id']]
            pending = any(item['assignment_id'] == prior['id'] and item['id'] not in records.get('review_attempt', {})
                          for item in records.get('review_invocation', {}).values())
            dead = not attempts and not pending and prior['route_id'] in retired
            if (pending or not attempts or any(item['status'] not in ('failed', 'incomplete') for item in attempts)) and not dead:
                raise ResearchError('review_resampling_forbidden', 'Retain the existing repair or source continuation without forking')
    history = []
    for ancestor in chain:
        prior_state = assignment_state(records, artifacts, ancestor['id'])
        allowed = {'review_context_contaminated', 'review_source_parent_unverified', 'review_context_repair_stale',
                   'review_route_retired'}
        if prior_state['output'] is None or any(item['code'] not in allowed for item in prior_state['obligations']):
            raise ResearchError('review_context_repair_unverified', 'Preserve only the exact observed scientific history with verified invocation provenance')
        packet = json.loads(artifacts.read(ancestor['packet']))
        from .bar_sources import pending
        if ancestor['role'] in ROLES and not pending(packet, prior_state['output']):
            finals = _reviews(records, {ancestor['id']})
            if not any({key: value for key, value in saved['payload'].items()
                        if key not in ('id', 'dossier_id', 'assignment_id')} == prior_state['output'] for saved in finals):
                raise ResearchError('review_context_repair_history_unrecorded',
                    'Record the exact observed final as historical scientific content before context reassessment',
                    {'assignment_id': ancestor['id']})
        history.append({'assignment_id': ancestor['id'], 'packet': packet,
                        'observed_output': prior_state['output'], 'independence': 'no_longer_qualified'})
    return {'predecessor': predecessor, 'slot_id': original_slot, 'packet': history[-1]['packet'],
            'historical_reviews': _reviews(records, {item['id'] for item in chain if item['role'] == predecessor['role']}),
            'scientific_history': history,
            'event_ids': sorted(repair['event_ids']), 'probe_id': repair['probe_id'],
            'cause': [{key: event[key] for key in ('id', 'kind', 'source', 'reason')} for event in events]}


def _scientific_content(output):
    content = {key: output.get(key) for key in ('stage', 'support', 'value', 'objections', 'limitations')}
    if isinstance(content['support'], dict):
        content['support'] = {key: value for key, value in content['support'].items() if key not in ('id', 'assessor')}
    return content


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
    if assignment.get('subject', {}).get('kind') != 'problem' or assignment['role'] not in ROLES:
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
    probe = records.get('review_route_probe', {}).get(repair['probe_id'])
    if (not events or probe is None or probe['route_id'] != bound['route_id']
            or probe['recorded_revision'] <= max(event['recorded_revision'] for event in events)
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
    for finding in findings:
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
            (evidence_validator or validate_evidence_contract)(records, artifacts, finding['evidence'], review_binding=binding)
        from .review_protocol import _reference_templates
        content = _reference_templates(observed_output) if packet is not None else observed_output
        if finding['disposition'] == 'confirmed' and _scientific_content(originals[finding['review_id']]) != _scientific_content(content):
            raise ResearchError('review_reassessment_mismatch', 'A changed scientific finding cannot be labeled confirmed')
    if seen != set(originals):
        raise ResearchError('review_reassessment_incomplete', 'Address every inherited final review')
