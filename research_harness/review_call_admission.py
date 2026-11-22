"""Transactional admission and launch checks shared by reviewer call writers."""

import time
import uuid

from .errors import ResearchError
from .review_budget import check_admission


def admit_review_call(transaction, operation, invocation_id, route_id, *, invocation=None, epoch_id=None, task_id=None):
    """Check the allowance, then shared store and route capacity, before a start."""
    from .review_task_execution import _TransactionRecords, _get, _policy
    records = _TransactionRecords(transaction)
    check_admission(records, operation, invocation_id)
    from .review_stop import build_call_scope, require_unstopped
    if records.get('review_stop'):
        require_unstopped(records, dict(kind='store'))
        require_unstopped(records, build_call_scope(records, operation, invocation, epoch_id=epoch_id))
    policy = _policy(records)
    route = _get(records, "review_route", route_id)
    if route.get("adapter") not in policy["covered_adapters"]:
        return
    active = list_active_calls(records, policy)
    waits = list_waiting_calls(records)
    own = next((row for row in waits if row['operation'] == operation and row['invocation_id'] == invocation_id), None)
    older = [row for row in waits if (own is None or row['requested_revision'] < own['requested_revision'])
             and can_admit_route(active, policy, row['route_id'])]
    if not can_admit_route(active, policy, route_id) or older:
        scope = build_call_scope(records, operation, invocation, epoch_id=epoch_id)
        assignment_id = (records['review_epoch'][epoch_id].get('controller_assignment_id') if epoch_id is not None
                         else (invocation or {}).get('assignment_id'))
        call = dict(operation=operation, invocation_id=invocation_id, route_id=route_id, scope=scope,
                    assignment_id=assignment_id, epoch_id=epoch_id, task_id=task_id)
        routes = records.get('review_route', {})
        before = active + [row for row in waits if own is None or row['requested_revision'] < own['requested_revision']]
        wait_seconds = sum(routes.get(row['route_id'], {}).get('configuration', {}).get('timeout_seconds', 600) + 60
                           for row in before)
        raise ResearchError('review_capacity_wait', 'Earlier reviewer calls hold the capacity or wait before this call. '
                            'Run the command again after they end, and reconcile a call whose process has ended.',
                            dict(reservations=[row['id'] for row in active], scope='store',
                                 calls_before=len(before), call=call, wait_seconds=wait_seconds))
    # Successful admission commits abandonment and the exact wait's completion
    # together with its invocation or reservation. Failed admission writes nothing.
    abandon_dead_waits(transaction)
    if own is not None:
        transaction.put('review_call_wait', own['id'], dict(own, state='admitted'))


def check_launch(records, transition, lifecycle, *, token=None, read_identity=None):
    """Check a launch claim, worker start or child registration in its write transaction.

    The writer retains authority and lock verification. Child registrations supply
    the process identity reader so live identity checks stay inside the transaction.
    Launches consume the existing admission without checking budget or capacity again.
    """
    from .review_task_execution import _check_references, _get
    from .review_stop import build_call_scope, require_unstopped
    is_child = transition in ('review.call_child', 'review.worker_child')
    if is_child:
        stop = records.get('review_stop', {}).get(lifecycle.get('stop_id'))
        if stop is not None and stop['release'] is None and stop['mode'] == 'now':
            require_unstopped(records, stop['scope'])
    else:
        require_unstopped(records, dict(kind='store'))
        require_unstopped(records, build_call_scope(records, 'review.task_reserve', epoch_id=lifecycle.get('epoch_id')))
    if transition == "review.worker_claim":
        if lifecycle["released"] or lifecycle["launch"] is not None or lifecycle["state"] != "reserved":
            raise ResearchError("review_invocation_pending", "Reconcile the original worker instead of launching twice")
        _check_references(records, _get(records, "review_execution_plan", lifecycle["plan_id"]))
    elif transition == "review.worker_running":
        if lifecycle["released"] or lifecycle["state"] != "reserved" or not lifecycle["launch"]["authorized"]:
            raise ResearchError("review_worker_reconciliation_required", "The worker's launch was not authorized")
        _check_references(records, _get(records, "review_execution_plan", lifecycle["plan_id"]))
    elif transition == "review.call_child":
        if (lifecycle["released"] or lifecycle["token"] != token
                or read_identity(lifecycle["owner"]["pid"]) != lifecycle["owner"]):
            raise ResearchError("review_worker_reconciliation_required", "The call's owner is no longer live")
    elif transition == "review.worker_child":
        if (lifecycle["released"] or lifecycle["state"] != "running" or lifecycle["token"] != token
                or read_identity(lifecycle["worker"]["pid"]) != lifecycle["worker"]):
            raise ResearchError("review_worker_reconciliation_required", "The transport child's worker is no longer live")


def list_active_calls(records, policy=None):
    from .review_task_execution import _active_legacy, _policy
    policy = _policy(records) if policy is None else policy
    memberships = records.get('review_pool_membership', {})
    active = [row for row in records.get('review_reservation', {}).values()
              if row['id'] in memberships and not row['released']]
    return active + _active_legacy(records, policy)


def can_admit_route(active, policy, route_id):
    return (len(active) < policy['store_limit'] and sum(row['route_id'] == route_id for row in active)
            < policy['route_limits'].get(route_id, policy['store_limit']))


def describe_route_scheduler(records, artifacts, route_ids, covered_adapters):
    """Suggest one call per verified route covered by the capacity policy."""
    from .review_protocol import route_state
    routes = records.get('review_route', {})
    route_limits = {identifier: 1 for identifier in sorted(route_ids)
                    if routes.get(identifier, {}).get('adapter') in covered_adapters
                    and route_state(records, artifacts, identifier)['context_status'] == 'verified_for_route'}
    return dict(store_limit=len(route_limits), route_limits=route_limits)


def describe_capacity_wait(records, artifacts, route_id, active, waits):
    """Project current limits and a scheduler for verified participating routes."""
    from .review_task_execution import _policy
    policy = _policy(records)
    own_active = sum(row['route_id'] == route_id for row in active)
    limits = []
    if len(active) >= policy['store_limit']:
        limits.append('store_limit')
    if own_active >= policy['route_limits'].get(route_id, policy['store_limit']):
        limits.append('route_limit')
    result = dict(blocking_limits=limits)
    if 'store_limit' in limits and not own_active and artifacts is not None:
        participants = {row['route_id'] for row in active + waits} | {route_id}
        scheduler = describe_route_scheduler(records, artifacts, participants, policy['covered_adapters'])
        if route_id in scheduler['route_limits']:
            result['scheduler'] = scheduler
    return result


def list_waiting_calls(records, *, live_only=True):
    from .review_stop import _has_live_identity
    return sorted((row for row in records.get('review_call_wait', {}).values()
                   if row['state'] == 'waiting' and (not live_only or _has_live_identity(row['owner']))),
                  key=lambda row: (row['requested_revision'], row['id']))


def abandon_dead_waits(transaction):
    from .review_stop import _has_live_identity
    for row in transaction.records('review_call_wait').values():
        if row['state'] == 'waiting' and not _has_live_identity(row['owner']):
            transaction.put('review_call_wait', row['id'], dict(row, state='abandoned'))


def record_wait(store, call, *, owner=None):
    from .review_worker import _identity
    from .review_stop import require_unstopped
    from .review_task_execution import _mutate_current, _TransactionRecords
    owner = _identity() if owner is None else owner
    identifier = 'review-call-wait:' + uuid.uuid4().hex
    def apply(tx, value, revision):
        records = _TransactionRecords(tx)
        check_admission(records, call['operation'], call['invocation_id'])
        require_unstopped(records, call['scope'])
        abandon_dead_waits(tx)
        old = tx.get('review_call_wait', identifier)
        if old is not None:
            return old
        row = dict(call, id=identifier, owner=owner, state='waiting', requested_revision=revision)
        tx.put('review_call_wait', identifier, row)
        return row
    return _mutate_current(store, 'review.call_wait', call, apply,
                           request_id=identifier)['result']


def withdraw_wait(store, identifier, reason):
    from .review_task_execution import _mutate_current
    def apply(tx, value, revision):
        row = tx.get('review_call_wait', identifier)
        if row['state'] == 'waiting':
            row = dict(row, state='withdrawn', reason=reason)
            tx.put('review_call_wait', identifier, row)
        return row
    return _mutate_current(store, 'review.call_withdraw', dict(id=identifier, reason=reason), apply,
                           request_id=identifier + ':withdraw:' + reason)['result']


def wait_for_admission(store, attempt):
    """Retry only capacity admission; the action re-prepares against the new revision."""
    from .review_stop import require_unstopped
    wait = None
    deadline = None
    capacity_error = None
    try:
        while True:
            if wait is not None:
                records = store.snapshot()['records']
                current = records['review_call_wait'][wait['id']]
                require_unstopped(records, current['scope'])
                if current['state'] == 'withdrawn':
                    from .stop_classes import classify_stop
                    raise ResearchError('review_chain_stopped', 'A review stop withdrew this waiting call, and the call does not resume. '
                                        'Start the command again; settle and release a stop that still stands first.',
                                        dict(stop_id=current.get('stop_id'), stop_class=classify_stop('review_chain_stopped')))
                if time.monotonic() >= deadline:
                    withdraw_wait(store, wait['id'], 'timeout')
                    raise capacity_error
            try:
                return attempt(wait is not None)
            except ResearchError as error:
                if error.code != 'review_capacity_wait':
                    raise
                capacity_error = error
                if wait is None:
                    wait = record_wait(store, error.details['call'])
                    deadline = time.monotonic() + error.details['wait_seconds']
                if time.monotonic() >= deadline:
                    withdraw_wait(store, wait['id'], 'timeout')
                    raise
                time.sleep(min(1, max(0, deadline - time.monotonic())))
    finally:
        if wait is not None:
            withdraw_wait(store, wait['id'], 'command_ended')
