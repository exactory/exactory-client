"""Durable stop boundaries and administrative settlement of owned reviewer calls."""

import os
import signal
import time
import uuid

from .errors import ResearchError
from .evidence import digest
from .operations import fields, text, immutable_record
from .review_budget import CALL_OPERATIONS
from .review_task_execution import (_get, _mutate_current, _TransactionRecords, identify_call,
                                    read_call_kinds, build_historical_attempt_step, find_reservation_dispatch,
                                    rebuild_call_payload)


STOP_MESSAGE = ('A review stop stands for this chain or for the store. '
                'Settle the calls of the stop and release it with review-stop before you start a call.')
STOP_GRACE_SECONDS = 10
OWNER_SETTLE_SECONDS = 120
CALL_COMMANDS = dict(zip(CALL_OPERATIONS, ('review-run', 'review-probe', 'review-output-correction')))


class ReviewStopSignal(BaseException):
    """Unwind ownership and transactions before completing a signal stop."""
    code = 'review_chain_stopped'

    def __init__(self, details):
        super().__init__('The series received a stop signal.')
        self.details = details

    def describe_error(self):
        return ResearchError(self.code, str(self), self.details).as_dict()


def build_chain_scope(records, assignment_id):
    """Use the planner's head and ancestry rules, including staged controllers."""
    from .review_continuation import _find_head, _list_lineage
    head = _find_head(records, None, assignment_id)
    root = _list_lineage(records, head)[0]
    return dict(kind='chain', root_assignment_id=root['id'], slot_id=head.get('slot_id'),
                role=head['role'], dossier_id=head.get('dossier_id'))


def contains_scope(scope, candidate):
    if scope['kind'] == 'store':
        return True
    if candidate['kind'] != 'chain':
        return False
    identity = ('slot_id', 'role', 'dossier_id') if scope['slot_id'] is not None else (
        'root_assignment_id', 'role', 'dossier_id')
    return all(scope[key] == candidate[key] for key in identity)


def build_call_scope(records, operation, invocation=None, *, epoch_id=None):
    if epoch_id is not None:
        assignment_id = _get(records, 'review_epoch', epoch_id).get('controller_assignment_id')
    else:
        assignment_id = (invocation or {}).get('assignment_id')
    return (build_chain_scope(records, assignment_id) if assignment_id in records.get('review_assignment', {})
            else dict(kind='store'))


def _matches_call_scope(records, scope, invocation=None, *, epoch_id=None):
    """Match stored calls by the planner's ancestry and slot identity."""
    if scope['kind'] == 'store':
        return True
    identifier = (records['review_epoch'][epoch_id].get('controller_assignment_id') if epoch_id is not None
                  else (invocation or {}).get('assignment_id'))
    assignment = records.get('review_assignment', {}).get(identifier)
    if assignment is None:
        return False
    staged = assignment.get('staged_review', {})
    if staged.get('kind') == 'task':
        assignment = records['review_assignment'][staged['controller_assignment_id']]
    if assignment['role'] != scope['role'] or assignment.get('dossier_id') != scope['dossier_id']:
        return False
    if scope['slot_id'] is not None and assignment.get('slot_id') == scope['slot_id']:
        return True
    from .review_continuation import _list_lineage
    return _list_lineage(records, assignment)[0]['id'] == scope['root_assignment_id']


def find_stop(records, scope=None, *, standing=True, assignment_id=None):
    rows = []
    for row in records.get('review_stop', {}).values():
        matches = (_matches_call_scope(records, row['scope'], dict(assignment_id=assignment_id))
                   if assignment_id is not None else contains_scope(row['scope'], scope))
        if matches and (not standing or row['release'] is None):
            rows.append(row)
    return max(rows, key=lambda row: row['requested_revision']) if rows else None


def require_unstopped(records, scope):
    stop = find_stop(records, scope)
    if stop is not None:
        from .stop_classes import classify_stop
        raise ResearchError('review_chain_stopped', STOP_MESSAGE,
                            dict(stop_id=stop['id'], mode=stop['mode'], reason=stop['reason'],
                                 stop_class=classify_stop('review_chain_stopped')))


def requires_capture_finish(records, reservation, captured):
    if captured is None:
        return False
    identifier = reservation['id']
    if (identifier in records.get('review_task_acceptance', {})
            or identifier in records.get('review_task_refusal', {})):
        return False
    if identifier not in records.get('review_capture_attempt', {}):
        return True
    return captured['observation']['status'] == 'completed'


def read_reservation_attempt(records, reservation):
    selected = records.get('review_capture_attempt', {}).get(reservation['id'], {})
    accepted = records.get('review_task_acceptance', {}).get(reservation['id'], {})
    identifier = selected.get('attempt_id') or accepted.get('receipt', {}).get('attempt', {}).get('id')
    return records.get('review_attempt', {}).get(identifier)


def list_scope_calls(records, scope):
    result = []
    for operation in CALL_OPERATIONS:
        kind, finished = read_call_kinds(operation)
        for invocation in records.get(kind, {}).values():
            member = records.get('review_pool_membership', {}).get(identify_call(operation, invocation['id']))
            if ((member is not None and not member['released'] or invocation['id'] not in records.get(finished, {}))
                    and _matches_call_scope(records, scope, invocation)):
                result.append(dict(operation=operation, invocation_id=invocation['id'],
                                   assignment_id=invocation.get('assignment_id')))
    for reservation in records.get('review_reservation', {}).values():
        captured = records.get('review_observation', {}).get(reservation['id'])
        pending_capture = requires_capture_finish(records, reservation, captured)
        if ((not reservation['released'] or pending_capture)
                and _matches_call_scope(records, scope, epoch_id=reservation['epoch_id'])):
            epoch = records['review_epoch'][reservation['epoch_id']]
            result.append(dict(operation='review.task_reserve', reservation_id=reservation['id'],
                               assignment_id=epoch.get('controller_assignment_id')))
    return result


def _has_live_identity(identity):
    from .review_worker import _process_table, _same_process
    return identity is not None and _same_process(_process_table([identity]).get(identity['pid']), identity)


def has_live_call_processes(lifecycle):
    from .review_worker import _process_table, _same_process, _has_processes
    owner = lifecycle.get('owner') or lifecycle.get('worker')
    children = lifecycle.get('children', [])
    identities = ([owner] if owner is not None else []) + children
    table = _process_table(identities)
    return ((owner is not None and _same_process(table.get(owner['pid']), owner))
            or any(_has_processes(child, table) for child in children))


def _reconcile_gone_drivers(transaction, records, scope, revision):
    for row in records.get('review_series_run', {}).values():
        if row['ended'] is None and contains_scope(scope, row['scope']) and not _has_live_identity(row['process']):
            transaction.put('review_series_run', row['id'], dict(row, ended=dict(code='process_gone', revision=revision)))


def list_live_drivers(records, scope):
    return [row for row in records.get('review_series_run', {}).values()
            if row['ended'] is None and contains_scope(scope, row['scope']) and _has_live_identity(row['process'])]


def build_reconciliation_step(records, call):
    if 'reservation_id' in call:
        reservation = records['review_reservation'][call['reservation_id']]
        epoch = records['review_epoch'][reservation['epoch_id']]
        return dict(command='review-run', payload=dict(assignment_id=epoch['controller_assignment_id'],
                    epoch_id=epoch['id'], task_id=reservation['task_id'], action='reconcile'))
    return dict(command='review-run', payload=dict(action='reconcile', operation=call['operation'],
                invocation_id=call['invocation_id']))


def _describe_call_step(records, call):
    step = build_reconciliation_step(records, call)
    if 'reservation_id' in call:
        reservation = records['review_reservation'][call['reservation_id']]
        capture = records.get('review_observation', {}).get(reservation['id'])
        dispatch = find_reservation_dispatch(records, reservation)
        if dispatch is not None and requires_capture_finish(records, reservation, capture):
            step = dict(command='review-run', request_id=dispatch[0],
                        payload=rebuild_call_payload('review.invoke', dispatch[1]))
        attempt = read_reservation_attempt(records, reservation)
        return reservation, capture, attempt, step
    operation, identifier = call['operation'], call['invocation_id']
    kind, finished = read_call_kinds(operation)
    invocation = records[kind][identifier]
    lifecycle_id = identify_call(operation, identifier)
    member = records.get('review_pool_membership', {}).get(lifecycle_id)
    capture = records.get('review_call_observation', {}).get(lifecycle_id)
    attempt = records.get(finished, {}).get(identifier)
    if capture is not None and attempt is None:
        step = dict(command=CALL_COMMANDS[operation], payload=rebuild_call_payload(operation, invocation),
                    request_id=capture['request_id'])
    return member, capture, attempt, step


def describe_call(records, call):
    """Read lifecycle facts without changing a scientific observation or its authentication."""
    member, capture, attempt, step = _describe_call_step(records, call)
    pending_capture = (requires_capture_finish(records, member, capture) if 'reservation_id' in call
                       else capture is not None and attempt is None)
    if member is None:
        kind, _ = read_call_kinds(call['operation'])
        state = 'settled' if attempt is not None else 'historical'
        if state == 'historical' and call['operation'] == 'review.invoke':
            step = build_historical_attempt_step(records[kind][call['invocation_id']])
    elif pending_capture:
        state = 'captured'
        if 'reservation_id' in call and 'request_id' not in step and capture['observation']['status'] == 'completed':
            step['payload']['action'] = 'accept'
    elif member['released']:
        state = 'settled'
    elif attempt is not None or member['state'] in ('accepted', 'refused'):
        state = 'accepted'
    else:
        state = 'running' if has_live_call_processes(member) else 'interrupted'
    row = dict(call, state=state)
    if state not in ('settled',):
        row['step'] = step
    return row


def describe_stop(records, stop):
    result = dict(stop, boundary_stands=stop['release'] is None,
                  call_states=[describe_call(records, call) for call in stop['calls']])
    result['running_drivers'] = [row['id'] for row in list_live_drivers(records, stop['scope'])
                                 if row['id'] in stop['drivers']]
    return result


def request_stop(store, payload, *, request_id, expected_revision=None, origin='command'):
    """Serialize scope, boundary, owned calls and withdrawn waits in one transaction."""
    fields(payload, ('id', 'action', 'mode', 'reason'), ('assignment_id', 'scope'))
    for key in ('id', 'reason'):
        text(payload[key], key)
    if (payload['action'] != 'stop' or payload['mode'] not in ('now', 'after_turn')
            or ('assignment_id' in payload) == ('scope' in payload)
            or 'scope' in payload and payload['scope'] != 'store'):
        raise ResearchError('invalid_input', 'The stop payload is not valid. Give action "stop", mode "now" or "after_turn", '
                            'and exactly one of assignment_id and scope "store".')
    if 'assignment_id' in payload:
        text(payload['assignment_id'], 'Assignment ID')
    def apply(tx, value, revision):
        records = _TransactionRecords(tx)
        scope = build_chain_scope(records, value['assignment_id']) if 'assignment_id' in value else dict(kind='store')
        # A store boundary also covers a chain request, while a new store stop
        # can cover several existing chain boundaries without releasing them.
        old = find_stop(records, scope)
        if old is None:
            stop = dict(id=value['id'], scope=scope, mode=value['mode'], reason=value['reason'], origin=origin,
                        calls=list_scope_calls(records, scope), waits=[],
                        drivers=[row['id'] for row in list_live_drivers(records, scope)],
                        requested_revision=revision, release=None)
            immutable_record(records, 'review_stop', stop['id'], stop)
        else:
            stop = dict(old, mode='now' if 'now' in (old['mode'], value['mode']) else 'after_turn')
        _reconcile_gone_drivers(tx, records, stop['scope'], revision)
        for call in stop['calls']:
            kind = 'review_reservation' if 'reservation_id' in call else 'review_pool_membership'
            key = call.get('reservation_id') or identify_call(call['operation'], call['invocation_id'])
            member = tx.get(kind, key)
            if member is not None:
                marked = records.get('review_stop', {}).get(member.get('stop_id'))
                boundary = (marked if marked is not None and marked['release'] is None
                            and marked['mode'] == 'now' else stop)
                stopped = dict(member, stop_id=boundary['id'], stop_mode=boundary['mode'])
                if kind == 'review_reservation':
                    tx.put('review_reservation', key, stopped)
                else:
                    tx.put('review_pool_membership', key, stopped)
            if kind == 'review_reservation':
                membership = tx.get('review_pool_membership', key)
                if membership is not None:
                    tx.put('review_pool_membership', key, dict(membership, stop_id=boundary['id'], stop_mode=boundary['mode']))
        waits = list(stop['waits'])
        for wait in records.get('review_call_wait', {}).values():
            if wait['state'] == 'waiting' and contains_scope(stop['scope'], wait['scope']):
                tx.put('review_call_wait', wait['id'], dict(wait, state='withdrawn', stop_id=stop['id']))
                waits.append(wait['id'])
        stop['waits'] = waits
        tx.put('review_stop', stop['id'], stop)
        return stop
    return _mutate_current(store, 'review.stop_request', payload, apply, request_id=request_id, expected_revision=expected_revision)


def _select_stop_calls(records, stop, scope):
    if scope is None or scope['kind'] == 'store':
        return stop
    selected = {(row['operation'], row.get('invocation_id'), row.get('reservation_id'))
                for row in list_scope_calls(records, scope)}
    return dict(stop, calls=[row for row in stop['calls']
        if (row['operation'], row.get('invocation_id'), row.get('reservation_id')) in selected])


def _end_owned_processes(records, stop):
    from .review_worker import _process_table, _has_processes, _same_process
    identities = []
    for call in stop['calls']:
        member, _, _, _ = _describe_call_step(records, call)
        if member is None or member['released']:
            continue
        children = member.get('children', [])
        identities.extend((child, True) for child in children)
        owner = member.get('worker') or member.get('owner')
        if owner is not None and (not children or 'reservation_id' in call):
            identities.append((owner, 'reservation_id' in call))
    def send(sig):
        alive = False
        for identity, group in identities:
            table = _process_table([identity])
            current = table.get(identity['pid'])
            live = (_has_processes(identity, table) and (current is None or _same_process(current, identity))
                    if group else _same_process(current, identity))
            if not live or identity['pid'] == os.getpid() or group and identity['pgid'] == os.getpgrp():
                continue
            alive = True
            try:
                (os.killpg if group else os.kill)(identity['pgid'] if group else identity['pid'], sig)
            except ProcessLookupError:
                pass
        return alive
    send(signal.SIGTERM)
    deadline = time.monotonic() + STOP_GRACE_SECONDS
    while any(_has_processes(identity, _process_table([identity])) if group else _has_live_identity(identity)
              for identity, group in identities if identity['pid'] != os.getpid()):
        if time.monotonic() >= deadline:
            send(signal.SIGKILL)
            break
        time.sleep(.1)


def settle_stop(store, stop_id, *, scope=None):
    """Settle uncaptured deaths or finish captures through the original public operation."""
    from .cli import OPERATIONS
    records = store.snapshot()['records']
    stop = _select_stop_calls(records, records['review_stop'][stop_id], scope)
    if stop['mode'] != 'now':
        return
    _end_owned_processes(records, stop)
    deadline = time.monotonic() + OWNER_SETTLE_SECONDS
    while True:
        records = store.snapshot()['records']
        pending = False
        for call in stop['calls']:
            row = describe_call(records, call)
            if row['state'] in ('settled', 'historical'):
                continue
            member, capture, _, _ = _describe_call_step(records, call)
            step = row['step']
            payload = step['payload']
            request_id = step.get('request_id') or 'review-stop-settle:' + uuid.uuid4().hex
            if row['state'] == 'captured' and 'invocation_id' in call:
                original = store.committed_request(capture['request_id'] + ':start')
                payload, request_id = original['payload'], capture['request_id']
            try:
                OPERATIONS[step['command']](store, payload, request_id=request_id,
                                           expected_revision=store.revision)
            except ResearchError as error:
                # A scientific refusal can follow a committed attempt; a live
                # owner or changed lock leaves an explicit administrative step.
                current = describe_call(store.snapshot()['records'], call)
                if current['state'] not in ('settled', 'historical'):
                    if error.code not in ('review_invocation_pending', 'review_worker_reconciliation_required',
                                          'store_busy', 'stale_revision'):
                        return
                    pending = True
            pending = pending or describe_call(store.snapshot()['records'], call)['state'] not in ('settled', 'historical')
        if not pending or time.monotonic() >= deadline:
            return
        time.sleep(.1)


def release_stop(store, payload, *, request_id, expected_revision=None):
    fields(payload, ('id', 'action', 'stop_id', 'reason'))
    for key in ('id', 'stop_id', 'reason'):
        text(payload[key], key)
    if payload['action'] != 'release':
        raise ResearchError('invalid_input', 'The release payload is not valid. Give action "release" and the stop_id of a recorded review stop.')
    def apply(tx, value, revision):
        records = _TransactionRecords(tx)
        stop = _get(records, 'review_stop', value['stop_id'])
        if stop['release'] is not None:
            return stop
        calls = [describe_call(records, call) for call in stop['calls']]
        waits = [row['id'] for row in records.get('review_call_wait', {}).values()
                 if row['state'] == 'waiting' and contains_scope(stop['scope'], row['scope'])]
        drivers = [row for row in list_live_drivers(records, stop['scope']) if row['id'] in stop['drivers']]
        if waits or drivers or any(call['state'] not in ('settled', 'historical') for call in calls):
            raise ResearchError('review_invocation_pending', 'The stop cannot be released yet: a call is not settled, a call waits, or an earlier driver runs. '
                                'A settled call needs nothing. Let a running call end, or send the stop again with mode "now", '
                                'which also withdraws the waiting calls. Run the step of each other listed call, '
                                'wait until the listed drivers end, and release again.',
                                dict(stop_id=stop['id'], calls=calls, waits=waits, drivers=[row['id'] for row in drivers]))
        result = dict(stop, release=dict(id=value['id'], reason=value['reason'], revision=revision))
        tx.put('review_stop', stop['id'], result)
        _reconcile_gone_drivers(tx, records, stop['scope'], revision)
        return result
    return _mutate_current(store, 'review.stop_release', payload, apply, request_id=request_id,
                           expected_revision=expected_revision)


def stop_review(store, payload, *, request_id, expected_revision=None):
    fields(payload, ('action',), None)
    if payload['action'] == 'release':
        return release_stop(store, payload, request_id=request_id, expected_revision=expected_revision)
    previous = store.committed_request(request_id)
    if previous is not None:
        return _mutate_current(store, 'review.stop', payload, lambda tx, value, revision: None,
                               request_id=request_id, expected_revision=expected_revision)
    receipt = request_stop(store, payload, request_id=request_id + ':request', expected_revision=expected_revision)
    records = store.snapshot()['records']
    stop = records['review_stop'][receipt['result']['id']]
    if stop['mode'] == 'now' and stop['release'] is None:
        settle_stop(store, stop['id'])
    def finish(tx, value, revision):
        records = _TransactionRecords(tx)
        return describe_stop(records, records['review_stop'][stop['id']])
    return _mutate_current(store, 'review.stop', payload, finish, request_id=request_id)


def start_driver(store, assignment_id):
    from .review_worker import _identity
    identifier = 'review-series-run:' + uuid.uuid4().hex
    identity = _identity()
    def apply(tx, value, revision):
        records = _TransactionRecords(tx)
        scope = build_chain_scope(records, assignment_id)
        _reconcile_gone_drivers(tx, records, scope, revision)
        row = dict(id=identifier, assignment_id=assignment_id, scope=scope,
                   process=identity, started_revision=revision, ended=None)
        tx.put('review_series_run', identifier, row)
        return row
    return _mutate_current(store, 'review.series_start', dict(assignment_id=assignment_id), apply,
                           request_id=identifier)['result']


def end_driver(store, identifier, last_line):
    def apply(tx, value, revision):
        row = tx.get('review_series_run', identifier)
        if row['ended'] is None:
            ended = dict(last_line, revision=revision)
            if 'revision_after_stop' in ended:
                ended['revision_after_driver'] = revision
            row = dict(row, ended=ended)
            tx.put('review_series_run', identifier, row)
        return row
    return _mutate_current(store, 'review.series_end', dict(id=identifier, last_line=last_line), apply,
                           request_id=identifier + ':end')['result']


def read_driver_boundary(store, driver):
    records = store.snapshot()['records']
    stop = find_stop(records, driver['scope'])
    return None if stop is None else describe_driver_stop(records, stop, driver['assignment_id'])


def describe_driver_stop(records, stop, assignment_id):
    from .stop_classes import describe_terminal
    from .review_plan_steps import describe_step
    row = describe_terminal('review_chain_stopped')
    row.update(state='stop', stop_id=stop['id'])
    row['details'].update(stop_id=stop['id'], **describe_stop(records, stop))
    assignment = records['review_assignment'][assignment_id]
    row['details']['step'] = describe_step(records, None, assignment, 10, stop_id=stop['id'])
    return row


def describe_closed_calls(records, artifacts, assignment):
    """List unfinished lifecycles and closed calls without usable output; every step closes recorded work."""
    from .review_failure_class import classify_attempt
    scope = build_chain_scope(records, assignment['id'])
    calls = []
    for operation in ('review.invoke', 'review.output_correction'):
        kind, finished = read_call_kinds(operation)
        for invocation in records.get(kind, {}).values():
            if not _matches_call_scope(records, scope, invocation):
                continue
            attempt = records.get(finished, {}).get(invocation['id'])
            member = records.get('review_pool_membership', {}).get(identify_call(operation, invocation['id']))
            active = member is not None and not member['released']
            if attempt is not None and not active:
                if operation == 'review.invoke' and classify_attempt(records, artifacts, attempt) is None:
                    continue
                if operation == 'review.output_correction' and attempt.get('correction_status') == 'accepted':
                    continue
            row = describe_call(records, dict(operation=operation, invocation_id=invocation['id'],
                                               assignment_id=invocation.get('assignment_id')))
            row['closed'] = row['state'] == 'settled'
            _complete_closed_call_step(row)
            calls.append(row)
    for reservation in records.get('review_reservation', {}).values():
        if not _matches_call_scope(records, scope, epoch_id=reservation['epoch_id']):
            continue
        attempt = read_reservation_attempt(records, reservation)
        if (reservation['released'] and reservation['id'] in records.get('review_task_acceptance', {})
                and attempt is not None
                and classify_attempt(records, artifacts, attempt) is None):
            continue
        row = describe_call(records, dict(operation='review.task_reserve', reservation_id=reservation['id'],
                                          assignment_id=assignment['id']))
        row['closed'] = row['state'] == 'settled'
        _complete_closed_call_step(row)
        calls.append(row)
    return calls


def record_signal_boundary(store, error):
    """Record a signal request after an interrupted store transaction has unwound."""
    payload = (error.details or {}).get('stop_request')
    if payload is None:
        return error.details['stop_id']
    return request_stop(store, payload, request_id=payload['id'], origin='signal')['result']['id']


def _complete_closed_call_step(row):
    if row['state'] == 'running':
        identifier = 'post-close-call:' + digest([row['operation'], row.get('invocation_id') or row.get('reservation_id')])
        row['step'] = dict(command='review-stop', payload=dict(id=identifier, action='stop',
            assignment_id=row['assignment_id'], mode='now', reason='The post closed this review chain.'))
