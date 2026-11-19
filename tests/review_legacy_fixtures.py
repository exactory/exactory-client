"""Functions of earlier releases, kept so that compatibility tests can compare the installed rules with them."""

from research_harness.operations import normalized_text


def list_turn_assignments_as_0_91_2(records, assignment):
    """research_harness.review_failure_class.list_turn_assignments of 0.91.2."""
    from research_harness.review_refusal_replacement import read_position_context
    capacity_chain = assignment.get('capacity_plan') and assignment.get('reading_chain')
    if capacity_chain:
        while assignment.get('capacity_replacement_of'):
            assignment = records['review_assignment'][assignment['capacity_replacement_of']]
    parent = assignment.get('context', {}).get('prior_assignment_id')
    result = []
    for other in records.get('review_assignment', {}).values():
        if other.get('role') != assignment['role']:
            continue
        context = other.get('context', {})
        if parent is not None:
            if (context.get('prior_assignment_id') == parent
                    and context.get('unread') == assignment.get('context', {}).get('unread')):
                result.append(other['id'])
            continue
        if any(other.get(key) != assignment.get(key) for key in ('route_id', 'dossier_id', 'subject')):
            continue
        if any(normalized_text(other[key]) != normalized_text(assignment[key])
               for key in ('reviewer_id', 'author_id')):
            continue
        if read_position_context(other) == read_position_context(assignment):
            result.append(other['id'])
    if capacity_chain:
        for other in sorted(records.get('review_assignment', {}).values(),
                            key=lambda row: row['recorded_revision']):
            if other.get('capacity_replacement_of') in result and other['id'] not in result:
                result.append(other['id'])
    return result
