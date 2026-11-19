"""Record and replay one final composed from ordinary selected observations.

FINAL_FAMILIES maps a family name to exactly three callbacks::

    list_turns(records, artifacts, assignment) -> None | {
        'chain': first_assignment_id, 'packet': logical_or_native_role_packet,
        'pages': [{'assignment_id': str, 'targets': [str]}],
        'closing': {'assignment_id': str, 'targets': [str]},
        'condensing': [{'assignment_id': str, 'targets': [str]}]}
    read_currency(records, artifacts, assignment) -> {
        'packet_digest': str, 'inventory_digest': str, 'series_key': str (optional)}
    validate_output(records, artifacts, assignment, packet, output) -> None

list_turns returns None unless the assignment belongs to a paged final of its
family; finals in one response also return None. It resolves selected replacement
assignments and lists the same final from each member turn, using only stored
bindings. packet is the role packet used by the composition rule and validator.
read_currency returns the currency recorded by the turn's page plan. A successor
plan can supersede pages at an earlier currency without changing those records.
Every member assignment binds capacity_plan in the ordinary capacity-plan form.
The stored plan owns targets.handles. Family lanes own their packet verification.

The two builders return (record, (artifact_descriptor, artifact_bytes)); neither
writes. The caller stores the descriptor's bytes and immutable review_composite
record in the same prepared mutation as the closing attempt. The record id is
that attempt's id; a reference to a composite is {id, digest(record)}.
"""

import copy
import json
from functools import partial

from .artifacts import describe_artifact
from .errors import ResearchError
from .evidence import digest
from .review_handles import expand_handles
from .storage import _canonical


COMPOSITE_PROTOCOL = 'exactory-review-composite-final-v1'
COMPOSITE_UNVERIFIED_MESSAGE = 'The composite final could not be authenticated. details.condition names the failed check.'


def build_composite(records, artifacts, assignment):
    """Build the exact record and output artifact from selected final turns."""
    from .review_protocol import read_assignment_output
    family, turns = _find_family(records, artifacts, assignment)
    if family is None:
        _refuse_composite(2)

    def describe_turn(turn):
        attempt, output = read_assignment_output(records, artifacts, turn['assignment_id'])
        if attempt is None or output is None:
            _refuse_composite(1, assignment_id=turn['assignment_id'])
        return dict(assignment_id=turn['assignment_id'], attempt_id=attempt['id'],
                    output_digest=digest(output), targets=copy.deepcopy(turn['targets'])), output

    pages = [describe_turn(turn) for turn in turns['pages']]
    closing, closing_output = describe_turn(turns['closing'])
    closing['condensing'] = [describe_turn(turn)[0] for turn in turns['condensing']]
    root = records['review_assignment'][turns['chain']]
    selected = records['review_assignment'][closing['assignment_id']]
    rule = find_composition_rule(selected['role'], turns['packet'])
    if rule is None:
        _refuse_composite(4)
    output = expand_handles(turns['packet'], rule['compose'](turns['packet'],
        [dict(targets=row['targets'], output=output) for row, output in pages], closing_output),
        artifacts=artifacts, assignment=selected)
    content = _canonical(output).encode('utf-8')
    reference = describe_artifact(content, 'application/json')
    record = dict(id=closing['attempt_id'], protocol=COMPOSITE_PROTOCOL,
        chain=dict(assignment_id=root['id'], plan_digest=root['capacity_plan']['digest']),
        composition=rule['id'], pages=[row for row, _ in pages], closing=closing,
        currency=family['read_currency'](records, artifacts, selected),
        output=dict(artifact=reference, digest=digest(output)))
    return record, (reference, content)


# Family lanes add their rows here. No process-local chain state is retained.
def _list_v7_final_turns(records, artifacts, assignment):
    from .openness_final_pages import list_composite_turns
    return list_composite_turns(records, artifacts, assignment)


def _read_v7_currency(records, artifacts, assignment):
    from .openness_final_pages import read_composite_currency
    return read_composite_currency(records, artifacts, assignment)


def _validate_v7_composite(records, artifacts, assignment, packet, output):
    from .openness_final_pages import validate_composite_output
    return validate_composite_output(records, artifacts, assignment, packet, output)


def _list_parts_final_turns(records, artifacts, assignment):
    from . import review_parts_v3
    return review_parts_v3.list_composite_turns(records, artifacts, assignment)


def _read_parts_currency(records, artifacts, assignment):
    from . import review_parts_v3
    return review_parts_v3.read_composite_currency(records, artifacts, assignment)


def _validate_parts_composite(records, artifacts, assignment, packet, output):
    from . import review_parts_v3
    return review_parts_v3.validate_composite_output(records, artifacts, assignment, packet, output)


def _list_session_final_turns(records, artifacts, assignment):
    from .review_session_v4 import list_composite_turns
    return list_composite_turns(records, artifacts, assignment)


def _read_session_currency(records, artifacts, assignment):
    from .review_session_v4 import read_composite_currency
    return read_composite_currency(records, artifacts, assignment)


def _validate_session_composite(records, artifacts, assignment, packet, output):
    from .review_session_v4 import validate_composite_output
    return validate_composite_output(records, artifacts, assignment, packet, output)


FINAL_FAMILIES = {
    'exactory-openness-reading-v7': dict(list_turns=_list_v7_final_turns,
        read_currency=_read_v7_currency, validate_output=_validate_v7_composite),
    # A math chain shares the v7 page plan; its validator is the one of its protocol description.
    'exactory-openness-reading-math-v2': dict(list_turns=_list_v7_final_turns,
        read_currency=_read_v7_currency, validate_output=_validate_v7_composite),
    'exactory-review-parts-v3': dict(list_turns=_list_parts_final_turns,
        read_currency=_read_parts_currency, validate_output=_validate_parts_composite),
    'exactory-independent-review-sources-v4': dict(list_turns=_list_session_final_turns,
        read_currency=_read_session_currency, validate_output=_validate_session_composite)}


def find_composition_rule(role, packet):
    """Return None or {id, compose}, binding the public rule and composer lazily.

    compose receives the role packet, pages in order as {targets, output} after
    handle expansion, and the recorded closing output.
    """
    from . import review_composition
    rule = review_composition.find_rule(role, packet)
    if rule is None:
        return None
    return dict(id=rule['id'], compose=partial(review_composition.compose_output, rule))


def _refuse_composite(condition, **details):
    raise ResearchError('review_composite_unverified', COMPOSITE_UNVERIFIED_MESSAGE,
                        dict(details, condition=condition))


def _find_family(records, artifacts, assignment):
    for family in FINAL_FAMILIES.values():
        turns = family['list_turns'](records, artifacts, assignment)
        if turns is not None:
            return family, turns
    return None, None


def _check_selected_attempts(records, artifacts, composite):
    """Condition 1: authenticate every selected ordinary observation and output."""
    from .review_protocol import authenticate_attempt, read_assignment_output
    authenticated = []
    try:
        rows = composite['pages'] + [composite['closing']] + composite['closing']['condensing']
        for row in rows:
            assignment = records['review_assignment'][row['assignment_id']]
            attempt = records['review_attempt'][row['attempt_id']]
            selected, output = read_assignment_output(records, artifacts, assignment['id'])
            if (attempt['assignment_id'] != assignment['id'] or selected is None or selected['id'] != attempt['id']
                    or not authenticate_attempt(records, artifacts, assignment, attempt)
                    or digest(output) != row['output_digest']):
                _refuse_composite(1, assignment_id=assignment['id'])
            authenticated.append((row, assignment, output))
    except (ResearchError, ValueError, TypeError, KeyError) as error:
        if isinstance(error, ResearchError) and error.code == 'review_composite_unverified':
            raise
        _refuse_composite(1)
    return authenticated


def _check_chain_currency(records, artifacts, composite, authenticated):
    """Condition 2: bind final turns to one chain, route, reviewer, plan and currency."""
    try:
        if (set(composite) != {'id', 'protocol', 'chain', 'composition', 'pages', 'closing', 'currency', 'output'}
                or composite['protocol'] != COMPOSITE_PROTOCOL
                or set(composite['chain']) != {'assignment_id', 'plan_digest'}
                or set(composite['output']) != {'artifact', 'digest'}):
            _refuse_composite(2)
        closing = records['review_assignment'][composite['closing']['assignment_id']]
        root = records['review_assignment'][composite['chain']['assignment_id']]
        family, turns = _find_family(records, artifacts, closing)
        if (family is None or turns['chain'] != root['id'] or composite['id'] != composite['closing']['attempt_id']
                or root['capacity_plan']['digest'] != composite['chain']['plan_digest']
                or closing['capacity_plan']['digest'] != composite['chain']['plan_digest']):
            _refuse_composite(2)
        for _, assignment, _ in authenticated:
            listed = family['list_turns'](records, artifacts, assignment)
            if (listed is None or listed['chain'] != root['id']
                    or assignment['route_id'] != root['route_id']
                    or assignment['reviewer_id'] != root['reviewer_id']
                    or assignment['role'] != root['role']
                    or assignment['capacity_plan']['digest'] != composite['chain']['plan_digest']
                    or family['read_currency'](records, artifacts, assignment) != composite['currency']):
                _refuse_composite(2, assignment_id=assignment['id'])
        if ([row['assignment_id'] for row in composite['pages']] !=
                [row['assignment_id'] for row in turns['pages']]
                or composite['closing']['assignment_id'] != turns['closing']['assignment_id']
                or [row['assignment_id'] for row in composite['closing']['condensing']] !=
                [row['assignment_id'] for row in turns['condensing']]):
            _refuse_composite(2)
    except (ResearchError, ValueError, TypeError, KeyError) as error:
        if isinstance(error, ResearchError) and error.code == 'review_composite_unverified':
            raise
        _refuse_composite(2)
    return family, turns, closing


def _check_page_targets(records, artifacts, composite, family, turns, authenticated):
    """Condition 3: pages partition the stored plan's targets exactly once."""
    from .review_route_capacity import read_chain_capacity_plan
    try:
        root = records['review_assignment'][composite['chain']['assignment_id']]
        plan = read_chain_capacity_plan(artifacts, root)
        seen = set()
        for page in composite['pages']:
            targets = page['targets']
            if (not isinstance(targets, list) or len(set(targets)) != len(targets)
                    or seen.intersection(targets)):
                _refuse_composite(3)
            seen.update(targets)
        if seen != set(plan['targets']['handles']):
            _refuse_composite(3)
        expected = turns['pages'] + [turns['closing']] + turns['condensing']
        for row, turn in zip([row for row, _, _ in authenticated], expected):
            keys = {'assignment_id', 'attempt_id', 'output_digest', 'targets'}
            if row is composite['closing']:
                keys.add('condensing')
            if set(row) != keys or row['targets'] != turn['targets']:
                _refuse_composite(3)
        # Each turn's packet-derived listing must agree with the closing listing.
        for _, assignment, _ in authenticated:
            listed = family['list_turns'](records, artifacts, assignment)
            if any(listed[key] != turns[key] for key in ('pages', 'closing', 'condensing')):
                _refuse_composite(3)
    except (ResearchError, ValueError, TypeError, KeyError) as error:
        if isinstance(error, ResearchError) and error.code == 'review_composite_unverified':
            raise
        _refuse_composite(3)


def _check_recomposed_output(artifacts, composite, turns, closing, authenticated):
    """Condition 4: replay the rule and handle expansion over authenticated bytes."""
    try:
        rule = find_composition_rule(closing['role'], turns['packet'])
        if (rule is None or rule['id'] != composite['composition']
                or not rule['id'].startswith('exactory-final-composition-v1/')):
            _refuse_composite(4)
        page_count = len(composite['pages'])
        output = rule['compose'](turns['packet'],
            [dict(targets=row['targets'], output=output) for row, _, output in authenticated[:page_count]],
            authenticated[page_count][2])
        output = expand_handles(turns['packet'], output, artifacts=artifacts, assignment=closing)
        recorded = json.loads(artifacts.read(composite['output']['artifact']))
        output_digest = digest(output)
        if digest(recorded) != output_digest or output_digest != composite['output']['digest']:
            _refuse_composite(4)
    except (ResearchError, ValueError, TypeError, KeyError) as error:
        if isinstance(error, ResearchError) and error.code == 'review_composite_unverified':
            raise
        _refuse_composite(4)
    return output


def _check_role_output(records, artifacts, family, closing, packet, output):
    """Condition 5: preserve the refusal and details of the role's validator."""
    family['validate_output'](records, artifacts, closing, packet, output)


def authenticate_composite(records, artifacts, composite):
    """Return the composed output only after the five stored-final checks pass."""
    authenticated = _check_selected_attempts(records, artifacts, composite)
    family, turns, closing = _check_chain_currency(records, artifacts, composite, authenticated)
    _check_page_targets(records, artifacts, composite, family, turns, authenticated)
    output = _check_recomposed_output(artifacts, composite, turns, closing, authenticated)
    _check_role_output(records, artifacts, family, closing, turns['packet'], output)
    return output


def read_final_output(records, artifacts, assignment):
    """Read an ordinary final or the composite bound to its selected closing turn."""
    from .review_protocol import read_assignment_output
    if isinstance(assignment, str):
        assignment = records['review_assignment'][assignment]
    attempt, output = read_assignment_output(records, artifacts, assignment['id'])
    candidates = [record for record in records.get('review_composite', {}).values()
                  if record.get('closing', {}).get('assignment_id') == assignment['id']]
    if not candidates:
        family, turns = _find_family(records, artifacts, assignment)
        if family is not None and turns['closing']['assignment_id'] == assignment['id']:
            _refuse_composite(1, assignment_id=assignment['id'])
        return output, None
    selected = [record for record in candidates if attempt is not None
                and record['closing'].get('attempt_id') == attempt['id']]
    if len(selected) != 1:
        _refuse_composite(1, assignment_id=assignment['id'])
    composite = selected[0]
    return authenticate_composite(records, artifacts, composite), composite


def prepare_composite(records, artifacts, assignment, attempt, closing_output):
    """Prepare a closing observation's composite before its atomic store mutation.

    closing_output has already passed the closing-page validator. The pending
    attempt's output artifact is available through the caller's staged artifacts.
    The family recorder writes this returned pair together with that attempt.
    """
    pending = records.copy()
    pending['review_attempt'] = dict(records.get('review_attempt', {}), **{attempt['id']: attempt})
    composite, artifact = build_composite(pending, artifacts, assignment)
    if composite['closing']['attempt_id'] != attempt['id'] or composite['closing']['output_digest'] != digest(closing_output):
        _refuse_composite(1, assignment_id=assignment['id'])
    family, turns = _find_family(pending, artifacts, assignment)
    closing = pending['review_assignment'][composite['closing']['assignment_id']]
    _check_role_output(pending, artifacts, family, closing, turns['packet'], json.loads(artifact[1]))
    return composite, artifact
