"""Execute a review chain through the public operation table."""

import copy
from datetime import datetime, timedelta, timezone
import json
import signal
import threading
import time
import uuid

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evidence import digest
from .output_views import render_workspace_views
from .review_continuation import plan_next, plan_route_handoff
from .stop_classes import REPLAN_CODES, describe_terminal


MAX_WAIT_SECONDS = 30 * 60
MAX_CONSECUTIVE_REFUSALS = 8
EXECUTABLE_STATES = frozenset(('run', 'resume', 'continue', 'replace', 'plan', 'record'))


def _emit_json(row):
    print(json.dumps(row, ensure_ascii=False, allow_nan=False), flush=True)


def _identify_action(plan):
    payload = copy.deepcopy(plan['payload'])
    # Only the action's new record identity is fresh; scientific IDs remain exact.
    if plan['state'] != 'resume':
        payload.pop('id', None)
    return digest([plan['command'], payload])


def _describe_stop(plan, code, details):
    terminal = describe_terminal(code)
    if code == 'review_series_no_progress':
        terminal.pop('command', None)
        terminal['details']['step'] = dict(command='gate-auto-pass', chooses=['stop_id'])
    if terminal['state'] == 'handoff' and terminal.get('command') != plan.get('command'):
        plan = {key: value for key, value in plan.items() if key != 'payload'}
    if terminal['state'] == 'continue':
        terminal['state'] = 'stop'
        terminal['details']['decision'] = dict(owner_decides='Whether to retry after the store changes.',
            then=[dict(answer='Inspect the record', steps=[dict(command='record-check')])])
    terminal['details'] = dict(details, **terminal['details'])
    return dict(plan, **terminal)


def _read_plan(store, assignment_id):
    snapshot = store.snapshot()
    from .evaluation import Evaluation
    from .review_provider_usage import describe_usage
    artifacts = Evaluation(snapshot['records'], ArtifactStore(store.root))
    plan = plan_next(snapshot['records'], artifacts, assignment_id)
    return snapshot, dict(plan, provider_usage=describe_usage(snapshot['records'], artifacts, plan=plan))


def _run_series(store, assignment_id, driver, *, emit=_emit_json):
    """Run until a recorded final, an external boundary, or a typed refusal."""
    from .cli import OPERATIONS
    from .review_worker import read_polling_store

    output = emit
    capacity_records = {}

    def emit(row, *, terminal=False, usage_snapshot=None):
        if row.get('error'):
            from .evaluation import Evaluation
            from .review_provider_usage import describe_usage
            try:
                current = store.snapshot() if usage_snapshot is None else usage_snapshot
                artifacts = Evaluation(current['records'], ArtifactStore(store.root))
                row = dict(row, provider_usage=describe_usage(current['records'], artifacts, plan=row))
            except (ResearchError, json.JSONDecodeError):
                # A diagnostic read failure keeps the original operation's error and last observed usage.
                pass
        if 'provider_usage' not in row:
            row = dict(row, provider_usage=last_plan.get('provider_usage', dict(windows=[], warnings=[])))
        if terminal and row.get('state') == 'stop' and row.get('code') and row.get('head_id') and row['code'] != 'review_chain_stopped':
            from .automatic_overrides import record_series_stop
            try:
                for attempt in range(2):
                    try:
                        # Each call takes a fresh snapshot and derives its own
                        # stop identity, scope and expected revision.
                        stop = record_series_stop(store, {
                            key: value for key, value in row.items() if key != 'provider_usage'})
                    except ResearchError as error:
                        if attempt == 0 and error.code == 'stale_revision':
                            continue
                        raise
                    break
                row = dict(row, stop_id=stop['id'], revision_after_stop=stop['recorded_revision'])
            except Exception as error:
                if isinstance(error, ResearchError) and error.code == 'review_chain_stopped':
                    raise
                # A stop that could not be recorded is never an automatic basis.
                recording_error = (error.as_dict() if isinstance(error, ResearchError) else
                                   dict(code='stop_record_error', message=str(error)))
                row = dict(row, stop_record_error=recording_error)
            finally:
                render_workspace_views(store.root)
        if row.get('code') == 'review_series_no_progress' and row.get('stop_id'):
            row = dict(row, details=dict(row['details'], step=dict(command='gate-auto-pass',
                payload=dict(stop_id=row['stop_id']), chooses=[])))
        if row.get('state') in ('stop', 'wait') and row.get('code'):
            from .stop_classes import classify_stop
            row = dict(row, stop_class=classify_stop(row['code']))
        head = row.get('head_id')
        if (row.get('state') not in ('done', 'handoff') and head is not None
                and head in capacity_records.get('review_assignment', {})):
            from .review_route_capacity import describe_assignment_warning
            warning = describe_assignment_warning(capacity_records, head, artifacts=store.build_artifact_store())
            if warning is not None:
                row = dict(row, capacity_warning=warning)
        if terminal or row.get('state') in ('done', 'handoff'):
            from .review_stop import end_driver
            ended = end_driver(store, driver['id'], row)['ended']
            if 'revision_after_stop' in row:
                row['revision_after_driver'] = ended['revision_after_driver']
            render_workspace_views(store.root)
        output(row)

    executed = {}
    refusals = 0
    ready_step = None
    last_plan = dict(state='stop', chain=None, head_id=assignment_id, progress=None)
    def check_boundary(*, settle=False):
        from .review_stop import read_driver_boundary
        row = read_polling_store(lambda: read_driver_boundary(store, driver))
        if row is not None:
            if settle and row['details']['mode'] == 'now':
                from .review_stop import settle_stop
                settle_stop(store, row['stop_id'], scope=driver['scope'])
                row = read_driver_boundary(store, driver) or row
            emit(dict(last_plan, **row), terminal=True)
            return True
        return False

    while True:
        try:
            if ready_step is None:
                snapshot, plan = _read_plan(store, assignment_id)
            else:
                snapshot, plan = ready_step
                ready_step = None
        except ResearchError as error:
            if error.code == 'review_chain_stopped':
                raise
            refusals += 1
            row = dict(last_plan, error=error.as_dict())
            if error.code not in REPLAN_CODES or refusals >= MAX_CONSECUTIVE_REFUSALS:
                emit(_describe_stop(row, error.code, error.details or {}), terminal=True)
                return 3
            emit(row)
            continue
        capacity_records = snapshot['records']
        last_plan = plan
        if plan.get('code') == 'review_chain_stopped':
            emit(dict(plan, state='stop', stop_id=plan['details']['stop_id']), terminal=True)
            return 3
        if check_boundary():
            return 3
        waiting = plan['state'] == 'wait'
        if plan['state'] == 'stop' and plan.get('code') in REPLAN_CODES:
            refusals += 1
            emit(plan, terminal=refusals >= MAX_CONSECUTIVE_REFUSALS)
            if refusals >= MAX_CONSECUTIVE_REFUSALS:
                return 3
            continue
        if plan['state'] in ('done', 'handoff', 'stop'):
            emit(plan, terminal=plan['state'] == 'stop')
            return 0 if plan['state'] == 'done' else 3
        if plan['state'] == 'wait':
            not_before = datetime.fromisoformat(plan['not_before'].replace('Z', '+00:00'))
            delay = max(0.0, (not_before - datetime.now(timezone.utc)).total_seconds())
            if delay > MAX_WAIT_SECONDS:
                emit(plan, terminal=True)
                return 4
            emit(plan)
            refresh_wait = False
            if delay:
                remaining = delay
                while remaining > 0:
                    interval = min(5, remaining)
                    time.sleep(interval)
                    remaining -= interval
                    if check_boundary():
                        return 3
                    if remaining > 0 and read_polling_store(lambda: store.revision) not in (None, snapshot['revision']):
                        refresh_wait = True
                        break
            # Another owner can capture, accept or finish during this wait.
            # Dispatch the newly selected state before acting on its revision.
            try:
                fresh_snapshot, fresh_plan = _read_plan(store, assignment_id)
            except ResearchError as error:
                if error.code not in REPLAN_CODES:
                    raise
                refusals += 1
                if refusals >= MAX_CONSECUTIVE_REFUSALS:
                    emit(_describe_stop(plan, error.code, error.details or {}), terminal=True)
                    return 3
                continue
            if (refresh_wait or fresh_plan['state'] != 'wait' or fresh_plan['head_id'] != plan['head_id']
                    or fresh_plan['progress'] != plan['progress']
                    or _identify_action(fresh_plan) != _identify_action(plan)):
                ready_step = fresh_snapshot, fresh_plan
                continue
            snapshot, plan = fresh_snapshot, fresh_plan
        elif plan['state'] not in EXECUTABLE_STATES:
            raise ValueError('Unknown review continuation state: ' + plan['state'])
        if check_boundary():
            return 3
        action = _identify_action(plan)
        position = (action, digest(plan['progress']))
        if not waiting and position in executed:
            emit(_describe_stop(plan, 'review_series_no_progress', dict(command=plan['command'],
                action_state=plan['state'], action_digest=action, progress_digest=position[1],
                successful_request_id=executed[position])), terminal=True)
            return 3
        request_id = plan.get('request_id') or 'review-series:' + uuid.uuid4().hex
        try:
            try:
                OPERATIONS[plan['command']](store, plan['payload'],
                    expected_revision=snapshot['revision'], request_id=request_id)
            finally:
                # Operations can commit before refusing. Match the CLI boundary
                # after each action, once its store locks have been released.
                render_workspace_views(store.root)
        except ResearchError as error:
            if error.code == 'review_chain_stopped':
                raise
            if check_boundary(settle=True):
                return 3
            if error.code == 'review_route_runtime_stale':
                assignment = snapshot['records']['review_assignment'][plan['head_id']]
                handoff = {key: plan[key] for key in ('chain', 'head_id', 'progress')}
                from .review_plan_steps import complete_plan
                emit(complete_plan(snapshot['records'], ArtifactStore(store.root), assignment,
                                   dict(handoff, **plan_route_handoff(assignment))))
                return 3
            row = dict(plan, request_id=request_id, error=error.as_dict())
            if ((waiting or plan['state'] == 'resume') and error.code in (
                    'review_worker_reconciliation_required', 'review_invocation_pending')):
                emit(row)
                if not waiting:
                    # A capture can precede its live owner's acceptance. The
                    # original request remains the recovery action after release.
                    time.sleep(1)
                refusals = 0
                continue
            if error.code in REPLAN_CODES:
                refusals += 1
                if refusals >= MAX_CONSECUTIVE_REFUSALS:
                    emit(_describe_stop(row, error.code, error.details or {}), terminal=True)
                    return 3
                emit(row)
                continue
            # Recording can persist an output refusal before raising it. Only
            # that durable transition permits the planner to offer a retry.
            try:
                current = store.snapshot()
            except ResearchError as read_error:
                refusals += 1
                row['read_error'] = read_error.as_dict()
                if read_error.code not in REPLAN_CODES or refusals >= MAX_CONSECUTIVE_REFUSALS:
                    emit(_describe_stop(row, read_error.code, read_error.details or {}), terminal=True)
                    return 3
                emit(row)
                continue
            before_refusals = snapshot['records'].get('review_output_refusal', {})
            new_refusals = current['records'].get('review_output_refusal', {})
            if (new_refusals != before_refusals or error.code == 'review_invocation_pending'):
                emit(row, usage_snapshot=current)
                refusals = 0
                continue
            terminal = _describe_stop(row, error.code, error.details or {})
            if terminal['state'] == 'wait':
                terminal['not_before'] = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
            if terminal['state'] in ('stop', 'handoff') and error.code != 'review_series_no_progress':
                from .review_plan_steps import complete_plan
                from .evaluation import Evaluation
                records = current['records']
                assignment = records.get('review_assignment', {}).get(plan['head_id'])
                if assignment is not None:
                    terminal = complete_plan(records, Evaluation(records, ArtifactStore(store.root)),
                                             assignment, terminal)
            emit(terminal, terminal=True, usage_snapshot=current)
            return 4 if terminal['state'] == 'wait' else 0 if terminal['state'] == 'done' else 3
        # Waiting reconciliations can be successful polls of a still-live
        # owner. Only executable continuation states spend a progress position.
        if not waiting:
            executed[position] = request_id
        refusals = 0
        row = dict(plan, request_id=request_id, revision=snapshot['revision'])
        try:
            ready_step = _read_plan(store, assignment_id)
        except ResearchError:
            # The next loop owns read-error recovery, after this executed step is emitted.
            ready_step = None
        else:
            row['provider_usage'] = ready_step[1]['provider_usage']
        emit(row)


def run_series(store, assignment_id, *, emit=_emit_json):
    """Record the driver lifetime and turn process signals into a requested stop."""
    from .review_stop import (start_driver, end_driver, request_stop, settle_stop,
                              read_driver_boundary, _end_owned_processes, record_signal_boundary, ReviewStopSignal, describe_driver_stop,
                              _select_stop_calls)
    driver = start_driver(store, assignment_id)
    previous = {}

    def handle_signal(number, frame):
        for handled in (signal.SIGTERM, signal.SIGINT):
            signal.signal(handled, signal.SIG_IGN)
        payload = dict(id='review-signal:' + uuid.uuid4().hex, action='stop', assignment_id=assignment_id,
                       mode='now', reason='The review series received ' + signal.Signals(number).name + '.')
        try:
            receipt = request_stop(store, payload, request_id=payload['id'], origin='signal')
        except ResearchError as error:
            if error.code not in ('store_reentrant_access', 'store_busy'):
                raise
            raise ReviewStopSignal(dict(stop_request=payload)) from error
        records = store.snapshot()['records']
        _end_owned_processes(records, _select_stop_calls(records, receipt['result'], driver['scope']))
        raise ReviewStopSignal(dict(stop_id=receipt['result']['id']))

    if threading.current_thread() is threading.main_thread():
        for number in (signal.SIGTERM, signal.SIGINT):
            previous[number] = signal.signal(number, handle_signal)
    try:
        return _run_series(store, assignment_id, driver, emit=emit)
    except (ResearchError, ReviewStopSignal) as error:
        if error.code != 'review_chain_stopped':
            raise
        stop_id = record_signal_boundary(store, error)
        settle_stop(store, stop_id, scope=driver['scope'])
        records = store.snapshot()['records']
        row = dict(describe_driver_stop(records, records['review_stop'][stop_id], assignment_id),
                   head_id=assignment_id, chain=None, progress=None)
        end_driver(store, driver['id'], row)
        render_workspace_views(store.root)
        emit(row)
        return 3
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        current = store.snapshot()['records']['review_series_run'][driver['id']]
        if current['ended'] is None:
            end_driver(store, driver['id'], dict(state='ended'))
            render_workspace_views(store.root)
