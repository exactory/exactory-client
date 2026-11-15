"""Finite continuity matrix and the completion contract for executed stop answers."""

STOP_END_MESSAGE = ('A stop answer did not end at an eligible assignment that follows the stopped head, at a recorded action, '
                    'or at a stated boundary without a serving route. Make the journey of the answer reach one of these ends.')
STOP_ANSWER_MESSAGE = ('The executed journeys do not match the answers of the stop, a journey did not run the commands of its '
                       'answer in order, or the stop code has no registry row. Execute exactly one journey for each answer, '
                       'remove a journey for an answer that the stop does not declare, and register the code in stop_classes.py.')
SIZE_MATRIX_MESSAGE = ('The continuity rows do not match the required task kinds and size outcomes, or a row lacks its retained '
                       'call or its executed boundary. Execute one complete row for each required case, and remove or correct '
                       'a row for a case that is not required.')
SIZE_OUTCOMES = (
    ('output_over_cap', None), ('output_over_cap', 'reduced_by_context'),
    ('input_over_limit', None), ('input_over_limit', 'compacted'),
    ('response_over_limit', None), ('timeout', None),
)


def list_size_cases(kinds):
    return [(kind, outcome, detail) for kind in kinds for outcome, detail in SIZE_OUTCOMES]


def validate_size_matrix(executed, kinds):
    """Require retained-call receipts and an executed boundary for every row."""
    covered = set()
    for row in executed:
        if not isinstance(row, dict):
            return [SIZE_MATRIX_MESSAGE]
        boundary = row.get('boundary', {})
        call = row.get('call', {})
        if not (row.get('attempt_id') and row.get('request_digest') and row.get('accepted_commands')
                and call.get('id') == row['attempt_id']
                and call.get('request_digest') == row['request_digest']
                and call.get('request') and call.get('recorded_revision')):
            return [SIZE_MATRIX_MESSAGE]
        if boundary.get('kind') == 'assignment':
            complete = (boundary.get('eligible') and boundary.get('id') != row.get('assignment_id')
                and boundary.get('prior_head') == row.get('assignment_id') and boundary.get('preflight')
                and boundary.get('record', {}).get('id') == boundary.get('id')
                and boundary.get('record', {}).get('recorded_revision')
                and boundary.get('subject') == row.get('subject'))
        else:
            receipt = boundary.get('receipt', {})
            complete = (boundary.get('kind') == 'action' and isinstance(receipt, dict)
                and receipt.get('id') and receipt.get('recorded_revision')
                and boundary.get('command') in row['accepted_commands'])
        if not complete:
            return [SIZE_MATRIX_MESSAGE]
        covered.add((row.get('kind'), row.get('outcome'), row.get('detail')))
    return [] if covered == set(list_size_cases(kinds)) else [SIZE_MATRIX_MESSAGE]


def validate_stop_journeys(stop, journeys, registry):
    """Check receipts produced by real-command journeys, never command names alone."""
    answers = stop.get('details', {}).get('decision', {}).get('then', [])
    if stop.get('code') not in registry or {row['answer'] for row in journeys} != {row['answer'] for row in answers}:
        return [STOP_ANSWER_MESSAGE]
    errors = []
    def list_commands(steps):
        commands = []
        for step in steps:
            if step.get('command'):
                commands.append(step['command'])
            commands.extend(list_commands(step.get('then', [])))
        return commands
    for journey in journeys:
        answer = next(row for row in answers if row['answer'] == journey['answer'])
        accepted = iter(journey.get('accepted_commands', []))
        for command in list_commands(answer.get('steps', [])):
            if not any(value == command for value in accepted):
                errors.append(STOP_ANSWER_MESSAGE)
                break
        end = journey.get('end', {})
        if end.get('kind') == 'assignment':
            complete = bool(end.get('eligible') and end.get('id') and
                end.get('prior_head') == stop.get('head_id'))
        elif end.get('kind') == 'action':
            complete = bool(end.get('receipt') and end.get('command') in journey.get('accepted_commands', []))
        else:
            complete = end.get('kind') == 'unserved' and bool(end.get('statement')) and not end.get('serving_routes')
        if not complete:
            errors.append(STOP_END_MESSAGE)
    return errors


def build_kind_packet(kind):
    """Small retained packet projections for the outcome counter, not admission fixtures."""
    if kind == 'reading':
        return dict(reading_turn={})
    if kind == 'final':
        return dict(reading_record={})
    if kind in ('read', 'combine', 'check'):
        return dict(review_parts=dict(kind=kind))
    if kind == 'request':
        return dict(reviewer_excerpts={})
    if kind == 'staged':
        return dict(staged_review={})
    if kind == 'probe':
        return dict(allowed_control='synthetic', evidence_control='synthetic')
    if kind == 'correction':
        return dict(protocol='exactory-reassessment-disposition-correction-v1')
    return {}


def describe_probe_boundary(outcome):
    # A probe is evidence, not an assignment; none of these failures admits a route.
    if outcome not in {row[0] for row in SIZE_OUTCOMES}:
        raise ValueError(SIZE_MATRIX_MESSAGE)
    return 'unverified'
