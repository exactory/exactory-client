"""Execute a review chain through the public operation table."""

import copy
from datetime import datetime, timezone
import json
import time
import uuid

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evidence import digest
from .review_continuation import plan_next, plan_route_handoff


MAX_WAIT_SECONDS = 30 * 60
MAX_CONSECUTIVE_REFUSALS = 8
REPLAN_CODES = frozenset(('store_busy', 'stale_revision', 'review_task_dependency_changed'))
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
    return dict(plan, state='stop', code=code, details=details)


def _read_plan(store, assignment_id):
    snapshot = store.snapshot()
    from .evaluation import Evaluation
    from .review_provider_usage import describe_usage
    artifacts = Evaluation(snapshot['records'], ArtifactStore(store.root))
    plan = plan_next(snapshot['records'], artifacts, assignment_id)
    return snapshot, dict(plan, provider_usage=describe_usage(snapshot['records'], artifacts, plan=plan))


def run_series(store, assignment_id, *, emit=_emit_json):
    """Run until a recorded final, an external boundary, or a typed refusal."""
    from .cli import OPERATIONS

    output = emit

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
        if terminal and row.get('state') == 'stop' and row.get('code') and row.get('head_id'):
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
                # A stop that could not be recorded is never an automatic basis.
                recording_error = (error.as_dict() if isinstance(error, ResearchError) else
                                   dict(code='stop_record_error', message=str(error)))
                row = dict(row, stop_record_error=recording_error)
        if row.get('state') in ('stop', 'wait') and row.get('code'):
            from .stop_classes import classify_stop
            row = dict(row, stop_class=classify_stop(row['code']))
        output(row)

    executed = {}
    refusals = 0
    ready_step = None
    last_plan = dict(state='stop', chain=None, head_id=assignment_id, progress=None)
    while True:
        try:
            if ready_step is None:
                snapshot, plan = _read_plan(store, assignment_id)
            else:
                snapshot, plan = ready_step
                ready_step = None
        except ResearchError as error:
            refusals += 1
            row = dict(last_plan, error=error.as_dict())
            if error.code not in REPLAN_CODES or refusals >= MAX_CONSECUTIVE_REFUSALS:
                emit(_describe_stop(row, error.code, error.details or {}), terminal=True)
                return 3
            emit(row)
            continue
        last_plan = plan
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
                emit(plan)
                return 4
            emit(plan)
            if delay:
                time.sleep(delay)
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
            if (fresh_plan['state'] != 'wait' or fresh_plan['head_id'] != plan['head_id']
                    or fresh_plan['progress'] != plan['progress']
                    or _identify_action(fresh_plan) != _identify_action(plan)):
                ready_step = fresh_snapshot, fresh_plan
                continue
            snapshot, plan = fresh_snapshot, fresh_plan
        elif plan['state'] not in EXECUTABLE_STATES:
            raise ValueError('Unknown review continuation state: ' + plan['state'])
        action = _identify_action(plan)
        position = (action, digest(plan['progress']))
        if not waiting and position in executed:
            emit(_describe_stop(plan, 'review_series_no_progress', dict(command=plan['command'],
                action_state=plan['state'], action_digest=action, progress_digest=position[1],
                successful_request_id=executed[position])), terminal=True)
            return 3
        request_id = plan.get('request_id') or 'review-series:' + uuid.uuid4().hex
        try:
            OPERATIONS[plan['command']](store, plan['payload'],
                expected_revision=snapshot['revision'], request_id=request_id)
        except ResearchError as error:
            if error.code == 'review_route_runtime_stale':
                assignment = snapshot['records']['review_assignment'][plan['head_id']]
                handoff = {key: plan[key] for key in ('chain', 'head_id', 'progress')}
                emit(dict(handoff, **plan_route_handoff(assignment)))
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
            emit(_describe_stop(row, error.code, error.details or {}), terminal=True, usage_snapshot=current)
            return 3
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
