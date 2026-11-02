"""Store-wide review allowance derived from canonical admission records."""

from .errors import ResearchError
from .operations import fields, prepared_mutation, text


CALL_OPERATIONS = ('review.invoke', 'review.route_probe', 'review.output_correction')
CHARGE_KINDS = ('review_invocation', 'review_attempt', 'review_probe_invocation', 'review_route_probe',
                'review_output_correction_invocation', 'review_output_correction', 'review_reservation')


def collect_charge_inventory(records):
    """Count starts and historical completions once in their operation namespace."""
    from .review_task_execution import read_call_kinds
    charges = {('review.task_reserve', key) for key in records.get('review_reservation', {})}
    for operation in CALL_OPERATIONS:
        invocation_kind, completion_kind = read_call_kinds(operation)
        for kind in (invocation_kind, completion_kind):
            for key, row in records.get(kind, {}).items():
                reservation_id = row.get('reservation_id') if operation == 'review.invoke' else None
                charges.add(('review.task_reserve', reservation_id) if reservation_id else (operation, key))
    return sorted(charges)


def read_budget_usage(records):
    """Project the optional allowance and cumulative expenditure, including unknown outcomes."""
    budget = records.get('review_budget', {}).get('store')
    maximum = budget['max_calls'] if budget is not None else None
    charged = len(collect_charge_inventory(records))
    return dict(configured=budget is not None, allowance=maximum, charged_calls=charged,
                remaining_calls=None if maximum is None else max(0, maximum - charged))


def check_admission(records, operation, identifier):
    """Check one prospective call against authoritative records in its start transaction."""
    budget = records.get('review_budget', {}).get('store')
    if budget is None:
        return
    charges = collect_charge_inventory(records)
    if (operation, identifier) in charges:
        raise ResearchError('record_conflict', 'A charged review identity cannot admit another call',
                            dict(operation=operation, id=identifier))
    maximum = budget['max_calls']
    if maximum is not None and len(charges) + 1 > maximum:
        raise ResearchError('review_budget_exhausted', 'The store review call allowance is exhausted',
                            read_budget_usage(records))


def record_budget(store, payload, *, expected_revision, request_id):
    """Record an opt-in cumulative allowance without resetting prior expenditure."""
    def prepare(records, value):
        fields(value, ('max_calls', 'reason'))
        maximum = value['max_calls']
        if maximum is not None and (type(maximum) is not int or maximum < 0):
            raise ResearchError('invalid_input', 'Maximum review calls must be a nonnegative integer or null')
        text(value['reason'], 'Allowance reason')
        charged = len(collect_charge_inventory(records))
        if maximum is not None and maximum < charged:
            raise ResearchError('review_budget_below_charged', 'The allowance is below admitted review calls',
                                dict(max_calls=maximum, charged_calls=charged))
        record = dict(value, id='store', recorded_revision=expected_revision + 1)
        return [('review_budget', 'store', record)], record
    return prepared_mutation(store, 'review.budget', payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)
