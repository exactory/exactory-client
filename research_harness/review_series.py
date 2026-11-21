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
from .stop_classes import HARNESS_DEFECT_CODES, REPLAN_CODES, WAITING_CODES, STOP_REGISTRY, describe_terminal


MAX_WAIT_SECONDS = 30 * 60
MAX_CONSECUTIVE_REFUSALS = 8
EXECUTABLE_STATES = frozenset(('run', 'resume', 'continue', 'replace', 'plan', 'record'))
EXECUTABLE_COMMANDS = frozenset(('review-run', 'review-assignment', 'value-review',
                                 'review-adjudication', 'problem-openness'))
ENDING_RECEIPTS_DIRECTORY = '.exactory/series-endings'
FALLBACK_STEP_MESSAGE = 'Run record-check on the workspace. Then run review-series on this head again.'
SESSION_FORECAST_FIELD = 'capacity_forecast'


def _emit_json(row):
    print(json.dumps(row, ensure_ascii=False, allow_nan=False), flush=True)


def _identify_action(plan):
    payload = copy.deepcopy(plan['payload'])
    # Only the action's new record identity is fresh; scientific IDs remain exact.
    if plan['state'] != 'resume':
        payload.pop('id', None)
    return digest([plan['command'], payload])


def _describe_stop(plan, code, details, *, refused_action=None):
    details = dict(copy.deepcopy(plan.get('details', {})), **details)
    details.pop('refused_action', None)
    terminal = describe_terminal(code, action=refused_action)
    if refused_action is not None:
        terminal['details']['refused_action'] = copy.deepcopy(refused_action)
    if code in HARNESS_DEFECT_CODES:
        terminal.pop('command', None)
        terminal['details']['step'] = dict(command='gate-auto-pass', chooses=['stop_id'])
    if terminal['state'] == 'handoff' and terminal.get('command') != plan.get('command'):
        plan = {key: value for key, value in plan.items() if key != 'payload'}
    if terminal['state'] == 'continue':
        terminal['state'] = 'stop'
        terminal['details']['decision'] = dict(owner_decides='Whether to retry after the store changes.',
            then=[dict(answer='Inspect the record', steps=[dict(command='record-check')])])
    if code == 'review_chain_projection_over_limit':
        terminal['details'] = dict(terminal['details'], **details)
    else:
        terminal['details'] = dict(details, **terminal['details'])
    return dict(plan, **terminal)


def _record_omission(row, member, error):
    details = row.setdefault('details', {})
    details.setdefault('omitted', {})[member] = (error.as_dict() if isinstance(error, ResearchError) else
                                               dict(code=type(error).__name__, message=str(error)))


def _render_views(store, row):
    try:
        summary = render_workspace_views(store.root)
        if summary is None or summary.get('errors'):
            # The public renderer retains failures instead of raising them.
            from .output_views import _read_marker
            marker = _read_marker(store.root)
            failure = marker.get('failure') or marker.get('errors')
            if failure:
                row.setdefault('details', {}).setdefault('omitted', {})['views'] = failure
    except BaseException as error:
        _record_omission(row, 'views', error)


def _read_plan(store, assignment_id):
    snapshot = store.snapshot()
    from .evaluation import Evaluation
    from .review_provider_usage import describe_usage
    artifacts = Evaluation(snapshot['records'], ArtifactStore(store.root))
    plan = plan_next(snapshot['records'], artifacts, assignment_id)
    try:
        plan = dict(plan, provider_usage=describe_usage(snapshot['records'], artifacts, plan=plan))
    except BaseException as error:
        _record_omission(plan, 'provider_usage', error)
    return snapshot, plan


def _enrich_line(store, row, capacity_records, *, usage_snapshot=None):
    row = copy.deepcopy(row)
    if row.get('error'):
        from .evaluation import Evaluation
        from .review_provider_usage import describe_usage
        try:
            current = store.snapshot() if usage_snapshot is None else usage_snapshot
            artifacts = Evaluation(current['records'], ArtifactStore(store.root))
            row['provider_usage'] = describe_usage(current['records'], artifacts, plan=row)
        except BaseException as error:
            _record_omission(row, 'provider_usage', error)
    if row.get('state') in ('stop', 'wait') and row.get('code'):
        from .stop_classes import classify_stop
        row['stop_class'] = classify_stop(row['code'])
    head = row.get('head_id')
    if (row.get('state') not in ('done', 'handoff') and head is not None
            and head in capacity_records.get('review_assignment', {})):
        from .review_route_capacity import describe_assignment_warning, describe_assignment_capacity
        assignment = capacity_records['review_assignment'][head]
        if 'capacity_plan_kind' in assignment:
            try:
                forecast = describe_assignment_capacity(capacity_records, store.build_artifact_store(), assignment)
                row['capacity_forecast'] = forecast['capacity_forecast']
            except BaseException as error:
                row.pop('capacity_forecast', None)
                _record_omission(row, 'capacity_forecast', error)
        try:
            warning = describe_assignment_warning(capacity_records, head, artifacts=store.build_artifact_store())
            if warning is not None:
                row['capacity_warning'] = dict(row.get('capacity_warning', {}), **warning)
        except BaseException as error:
            _record_omission(row, 'capacity_warning', error)
    if (SESSION_FORECAST_FIELD not in row and row.get('state') != 'done' and head is not None
            and head in capacity_records.get('review_assignment', {})
            and 'capacity_plan_kind' not in capacity_records['review_assignment'][head]):
        from .review_route_capacity import describe_session_forecast
        try:
            forecast = describe_session_forecast(capacity_records, store.build_artifact_store(),
                                                 capacity_records['review_assignment'][head])
            if forecast is not None:
                row[SESSION_FORECAST_FIELD] = forecast
        except BaseException as error:
            _record_omission(row, SESSION_FORECAST_FIELD, error)
    if head in capacity_records.get('review_assignment', {}):
        assignment = capacity_records['review_assignment'][head]
        if assignment.get('reading_chain'):
            from . import openness_reading, review_protocol
            try:
                artifacts = store.build_artifact_store()
                packet = review_protocol._read_json(artifacts, assignment['packet'])
                route = capacity_records['review_route'][assignment['route_id']]
                from .review_refusal_replacement import read_position_context
                logical = review_protocol.build_packet(capacity_records, artifacts, assignment['dossier_id'],
                    assignment['role'], assignment['reviewer_id'], **read_position_context(assignment))
                request = review_protocol.build_assignment_request(capacity_records, artifacts, assignment)
                receipt = openness_reading.describe_reading_receipt(capacity_records, artifacts, assignment,
                    route, logical, packet, request, admit_turn=False)
                row.update(capacity_forecast=receipt['capacity_forecast'],
                           capacity_warning=dict(row.get('capacity_warning', {}), **receipt['capacity_warning']))
            except BaseException as error:
                _record_omission(row, 'capacity_forecast', error)
                _record_omission(row, 'capacity_warning', error)
                if isinstance(error, (ResearchError, ValueError)):
                    row['capacity_forecast'] = dict(status='unavailable', is_bound=False,
                        message=openness_reading.FORECAST_NO_BOUND_MESSAGE,
                        reason=error.code if isinstance(error, ResearchError) else type(error).__name__)
    return row


def _build_check_step(store, head):
    return dict(command='record-check', arguments={'--workspace': str(store.root)},
                payload={}, chooses=[], prerequisites=[],
                then=[dict(command='review-series', arguments={
                    '--workspace': str(store.root), '--assignment': head},
                    payload={}, chooses=[], prerequisites=[])])


def _use_fallback_step(store, row):
    details = row.setdefault('details', {})
    details.setdefault('intended_step', copy.deepcopy(details.get('step', {})))
    details['step'] = dict(_build_check_step(store, row['head_id']), instruction=FALLBACK_STEP_MESSAGE)
    row.update(state='handoff', command='record-check')
    row.pop('payload', None)


def _complete_ending_step(store, row):
    details = row.setdefault('details', {})
    if row['state'] == 'done':
        details.pop('step', None)
        details.pop('decision', None)
        row.pop('command', None)
        row.pop('payload', None)
        return
    if row['state'] == 'wait':
        row.setdefault('not_before', (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat())
        details['step'] = dict(command='review-series', arguments={
            '--workspace': str(store.root), '--assignment': row['head_id']}, payload={}, chooses=[],
            prerequisites=[dict(not_before=row['not_before'])])
        return
    if 'step' not in details:
        answers = details.get('decision', {}).get('then', [])
        steps = answers[0].get('steps', []) if answers else []
        details['step'] = copy.deepcopy(steps[0] if steps else dict(
            command=row.get('command', 'record-check'), payload=row.get('payload', {}),
            chooses=[], prerequisites=[]))
    step = details['step']
    planned_step = copy.deepcopy(step)
    step.setdefault('payload', {})
    step.setdefault('chooses', [])
    step.setdefault('prerequisites', [])
    arguments = step.setdefault('arguments', {})
    if step['command'].startswith('search '):
        if step.get('root') is not None:
            arguments.setdefault('--attack-root', step['root'])
    else:
        arguments.setdefault('--workspace', str(store.root))
    if step['command'] == 'record-check':
        step.update(chooses=[], prerequisites=[])
    if step['command'] == 'review-series':
        step['arguments']['--assignment'] = row['head_id']
    if row['state'] == 'stop':
        if step['command'] == 'gate-auto-pass' or not details.get('decision'):
            details['decision'] = dict(owner_decides='How should this ending of the series be answered?',
                then=[dict(answer='Take the step of this ending', steps=[copy.deepcopy(step)])])
        for answer in details['decision'].get('then', []):
            for choice in answer.get('steps', []):
                # Only the step of the ending takes its completed form; another step of its command keeps its own.
                is_ending_step = choice == planned_step
                arguments = choice.setdefault('arguments', {})
                if choice['command'].startswith('search '):
                    if choice.get('root') is not None:
                        arguments.setdefault('--attack-root', choice['root'])
                else:
                    arguments.setdefault('--workspace', str(store.root))
                if choice.get('command') == 'review-series':
                    choice['arguments']['--assignment'] = row['head_id']
                if is_ending_step:
                    choice.update(copy.deepcopy(step))


def _bind_ending_receipt(row, identifier, revision):
    """Keep a requested stop's release identity beside its series receipt."""
    if row.get('code') == 'review_chain_stopped':
        row['series_stop_id'] = identifier
    else:
        row['stop_id'] = identifier
    row['revision_after_stop'] = revision


def _identify_ending(row):
    action = row.get('details', {}).get('refused_action')
    # Earlier planner receipts describe plan_next, without an executable payload.
    identity = (None if action is None else copy.deepcopy(action) if 'payload' not in action else
                [action['command'], action['state'], _identify_action(action)])
    result = [row['head_id'], row.get('progress'), row.get('code'), row['state'],
              row.get('details', {}).get('step', {}).get('command'), identity]
    if row.get('code') == 'review_chain_stopped':
        result.append(row.get('details', {}).get('stop_id') or row.get('stop_id'))
    return result


def _recover_ending_line(row):
    result = {key: copy.deepcopy(row[key]) for key in
              ('state', 'head_id', 'code', 'stop_class', 'error', 'stop_id', 'series_stop_id') if key in row}
    if row['state'] == 'wait':
        result['not_before'] = row['not_before']
    keys = ['intended_step', 'refused_action', 'unlisted', 'receipt', 'omitted', 'stop_id']
    if row['state'] == 'stop':
        keys.append('decision')
    elif row['state'] in ('handoff', 'wait'):
        keys.append('step')
    result['details'] = {key: copy.deepcopy(row['details'][key]) for key in keys if key in row['details']}
    return result


def _write_ending_file(store, row):
    from .native_writes import write_atomic
    from .workspace import checked_parent
    path = ENDING_RECEIPTS_DIRECTORY + '/' + uuid.uuid4().hex + '.json'
    row['details']['receipt'] = dict(place='workspace', path=path, retained_at=time.time_ns())
    with checked_parent(store.root, path, create=True) as (directory, name):
        write_atomic(name, json.dumps(row, ensure_ascii=False, allow_nan=False).encode(), directory_fd=directory)
    return path


def _end_series(store, plan, *, error=None, exit_code, output, capacity_records=None, current=None,
                refused_action=None, snapshot_failed=False, driver=None):
    """Fix the ending in memory, enrich it, retain it, then print it."""
    # Phase 1: these values come only from the selected plan and the refusal.
    row = (_describe_stop(dict(plan, error=error.as_dict()), error.code, error.details or {}, refused_action=refused_action)
           if error is not None else copy.deepcopy(plan))
    code = row.get('code')
    if code:
        if code not in STOP_REGISTRY and error is None:
            row = _describe_stop(row, code, row.get('details', {}))
        from .stop_classes import classify_stop
        row['stop_class'] = classify_stop(code)
    row.setdefault('details', {})
    if refused_action is None:
        row['details'].pop('refused_action', None)
    else:
        row['details']['refused_action'] = copy.deepcopy(refused_action)
    had_step = 'step' in row['details']
    _complete_ending_step(store, row)
    intended_step = copy.deepcopy(row['details'].get('step', {}))
    if not had_step:
        row['details'].pop('step', None)

    # Phase 2: each reading is optional and leaves the first-phase code intact.
    observed = current
    if observed is None:
        try:
            observed = store.snapshot()
        except BaseException as read_error:
            _record_omission(row, 'snapshot', read_error)
            snapshot_failed = True
    records = observed['records'] if observed is not None else (capacity_records or {})
    if observed is not None:
        row.setdefault('revision', observed['revision'])
    if code and row['state'] in ('handoff', 'stop'):
        try:
            from .review_plan_steps import complete_plan
            from .evaluation import Evaluation
            assignment = records.get('review_assignment', {}).get(row['head_id'])
            if assignment is not None and code != 'review_series_no_progress':
                if code == 'review_route_runtime_stale' and error is not None:
                    handoff = plan_route_handoff(assignment)
                    row.update(handoff, details=dict(row['details'], **handoff.get('details', {})))
                planned_state = row['state']
                row = complete_plan(records, Evaluation(records, ArtifactStore(store.root)), assignment, row)
                if error is None:
                    row['state'] = planned_state
            elif observed is None and intended_step.get('command') != 'record-check':
                row['details'].setdefault('step', intended_step)
                _use_fallback_step(store, row)
        except BaseException as detail_error:
            _record_omission(row, 'step', detail_error)
            if intended_step.get('command') != 'record-check':
                row['details'].setdefault('step', intended_step)
                _use_fallback_step(store, row)
    try:
        row = _enrich_line(store, row, records, usage_snapshot=observed)
    except BaseException as detail_error:
        _record_omission(row, 'diagnostics', detail_error)
    _complete_ending_step(store, row)
    if ((snapshot_failed or code == 'review_chain_stopped' and not row['details'].get('stop_id'))
            and row['state'] in ('handoff', 'stop')):
        _use_fallback_step(store, row)

    # Phase 3: the store, then a durable workspace file, then an explicit absence.
    if code:
        from .automatic_overrides import record_series_stop
        try:
            for attempt in range(2):
                try:
                    stop = record_series_stop(store, {key: value for key, value in row.items() if key != 'provider_usage'})
                except ResearchError as recording_error:
                    if attempt == 0 and recording_error.code == 'stale_revision':
                        continue
                    raise
                break
            _bind_ending_receipt(row, stop['id'], stop['recorded_revision'])
            if stop.get('read_errors'):
                row['details'].setdefault('omitted', {}).update(stop['read_errors'])
            if stop.get('read_errors') and row['details'].get('step', {}).get('command') == 'gate-auto-pass':
                _use_fallback_step(store, row)
            elif row['details'].get('step', {}).get('command') == 'gate-auto-pass':
                row['details']['step'].update(payload=dict(stop_id=stop['id']), chooses=[], prerequisites=[])
            _complete_ending_step(store, row)
            row['details']['receipt'] = copy.deepcopy(stop['plan'].get('details', {}).get('receipt',
                dict(place='store', id=stop['id'])))
        except BaseException as recording_error:
            _record_omission(row, 'stop_record', recording_error)
            row['stop_record_error'] = (recording_error.as_dict() if isinstance(recording_error, ResearchError) else
                                        dict(code='stop_record_error', message=str(recording_error)))
            if row['details'].get('step', {}).get('command') == 'gate-auto-pass':
                _use_fallback_step(store, row)
            try:
                _write_ending_file(store, row)
            except BaseException as file_error:
                _record_omission(row, 'receipt_file', file_error)
                row['details']['receipt'] = dict(place=None, errors=dict(
                    store=row['stop_record_error'], workspace=row['details']['omitted']['receipt_file']))
                if row['state'] in ('handoff', 'stop') and row['details'].get('step', {}).get('command') == 'record-check':
                    _use_fallback_step(store, row)

    # Phase 4: the receipt already exists when rendering or printing fails.
    if driver is not None:
        from .review_stop import end_driver
        try:
            ended = end_driver(store, driver['id'], row)['ended']
            if 'revision_after_stop' in row:
                row['revision_after_driver'] = ended['revision_after_driver']
        except BaseException as driver_error:
            _record_omission(row, 'driver', driver_error)
    try:
        _render_views(store, row)
    except BaseException as view_error:
        _record_omission(row, 'views', view_error)
    try:
        output(row)
    except BaseException as output_error:
        recovered = _recover_ending_line(row)
        _record_omission(recovered, 'output', output_error)
        print('', flush=True)
        output(recovered)
    return 0 if row['state'] == 'done' else exit_code


def _describe_requested_ending(store, error, plan, driver):
    """Keep the selected head and action while settling a requested stop."""
    from .review_stop import record_signal_boundary, settle_stop, describe_driver_stop
    raised = error.as_dict() if isinstance(error, ResearchError) else error.describe_error()
    row = _describe_stop(dict(plan, error=raised), 'review_chain_stopped', error.details or {})
    row['state'] = 'stop'
    try:
        stop_id = record_signal_boundary(store, error)
        row['stop_id'] = stop_id
        row['details']['stop_id'] = stop_id
        settle_stop(store, stop_id, scope=driver['scope'] if driver is not None else None)
        records = store.snapshot()['records']
        row.update(describe_driver_stop(records, records['review_stop'][stop_id], plan['head_id']))
    except BaseException as stop_error:
        _record_omission(row, 'stop_settlement', stop_error)
    return row


class _ProgressOutputFailure(BaseException):
    def __init__(self, error):
        self.error = error


def _run_series(store, assignment_id, driver, *, emit=_emit_json):
    """Run until a recorded final, an external boundary, or a typed refusal."""
    from .cli import OPERATIONS
    from .review_worker import read_polling_store
    from .review_stop import ReviewStopSignal
    capacity_records = {}

    def emit_progress(row, *, usage_snapshot=None):
        try:
            row = _enrich_line(store, row, capacity_records, usage_snapshot=usage_snapshot)
        except BaseException as read_error:
            row = copy.deepcopy(row)
            _record_omission(row, 'diagnostics', read_error)
        try:
            emit(row)
        except (ResearchError, ReviewStopSignal):
            raise
        except BaseException as output_error:
            raise _ProgressOutputFailure(output_error) from output_error

    executed = {}
    refusals = 0
    ready_step = None
    # A refusal whose details name the request of the step says that the same request goes on: after a recorded run
    # whose output refusal the busy store refused, that request records the refusal. The series repeats that step with
    # its request in place of a new plan until a step succeeds.
    repeated_step = None
    last_plan = dict(state='stop', chain=None, head_id=assignment_id, progress=None)
    def read_boundary(*, settle=False):
        from .review_stop import read_driver_boundary
        row = read_polling_store(lambda: read_driver_boundary(store, driver))
        if row is not None:
            if settle and row['details']['mode'] == 'now':
                from .review_stop import settle_stop
                settle_stop(store, row['stop_id'], scope=driver['scope'])
                row = read_driver_boundary(store, driver) or row
            return dict(last_plan, **row)
        return None

    while True:
        selected_action = None
        try:
            try:
                if repeated_step is not None:
                    snapshot, plan = repeated_step
                elif ready_step is None:
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
                    ending = dict(plan=row, error=error, exit_code=3, current=None)
                    break
                emit_progress(row)
                continue
            capacity_records = snapshot['records']
            if 'provider_usage' not in plan and 'provider_usage' in last_plan:
                plan = dict(plan, provider_usage=last_plan['provider_usage'])
            if plan['state'] == 'done' and SESSION_FORECAST_FIELD in last_plan:
                plan = dict(plan, **{SESSION_FORECAST_FIELD: last_plan[SESSION_FORECAST_FIELD]})
            last_plan = plan
            if plan.get('code') == 'review_chain_stopped':
                ending = dict(plan=dict(plan, state='stop', stop_id=plan['details']['stop_id']),
                              error=None, exit_code=3, current=None)
                break
            boundary = read_boundary()
            if boundary is not None:
                ending = dict(plan=boundary, error=None, exit_code=3, current=None)
                break
            waiting = plan['state'] == 'wait'
            if plan['state'] == 'stop' and plan.get('code') in REPLAN_CODES:
                refusals += 1
                if refusals >= MAX_CONSECUTIVE_REFUSALS:
                    ending = dict(plan=plan, error=None, exit_code=3, current=None)
                    break
                emit_progress(plan)
                continue
            if plan['state'] in ('done', 'handoff', 'stop'):
                ending = dict(plan=plan, error=None, exit_code=0 if plan['state'] == 'done' else 3, current=None)
                break
            if plan['state'] == 'wait':
                not_before = datetime.fromisoformat(plan['not_before'].replace('Z', '+00:00'))
                delay = max(0.0, (not_before - datetime.now(timezone.utc)).total_seconds())
                if delay > MAX_WAIT_SECONDS:
                    ending = dict(plan=plan, error=None, exit_code=4, current=None)
                    break
                emit_progress(plan)
                refresh_wait = False
                if delay:
                    remaining = delay
                    while remaining > 0:
                        interval = min(5, remaining)
                        time.sleep(interval)
                        remaining -= interval
                        boundary = read_boundary()
                        if boundary is not None:
                            break
                        if remaining > 0 and read_polling_store(lambda: store.revision) not in (None, snapshot['revision']):
                            refresh_wait = True
                            break
                    if boundary is not None:
                        ending = dict(plan=boundary, error=None, exit_code=3, current=None)
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
                        ending = dict(plan=plan, error=error, exit_code=3, current=None)
                        break
                    continue
                selected_action = fresh_plan
                if 'provider_usage' not in fresh_plan and 'provider_usage' in plan:
                    fresh_plan = dict(fresh_plan, provider_usage=plan['provider_usage'])
                if (refresh_wait or fresh_plan['state'] != 'wait' or fresh_plan['head_id'] != plan['head_id']
                        or fresh_plan['progress'] != plan['progress']
                        or _identify_action(fresh_plan) != _identify_action(plan)):
                    ready_step = fresh_snapshot, fresh_plan
                    continue
                snapshot, plan = fresh_snapshot, fresh_plan
                capacity_records = snapshot['records']
                last_plan = plan
            elif plan['state'] not in EXECUTABLE_STATES:
                raise ValueError('Unknown review continuation state: ' + plan['state'])
            boundary = read_boundary()
            if boundary is not None:
                ending = dict(plan=boundary, error=None, exit_code=3, current=None)
                break
            selected_action = plan
            action = _identify_action(plan)
            position = (action, digest(plan['progress']))
            if not waiting and position in executed:
                ending = dict(plan=plan, error=ResearchError('review_series_no_progress',
                    'The driver executed this action once with success, and the progress of the review did not '
                    'change, so the plan names the same action again. The driver does not execute it a second '
                    'time at the same progress. Take the step of the last line. When the step names '
                    'gate-auto-pass, request the automatic pass. When it names record-check, run it, and then '
                    'run review-series again.', dict(command=plan['command'],
                    action_state=plan['state'], action_digest=action, progress_digest=position[1],
                    successful_request_id=executed[position])), exit_code=3, current=None)
                break
            request_id = plan.get('request_id') or 'review-series:' + uuid.uuid4().hex
            try:
                try:
                    if plan['command'] not in EXECUTABLE_COMMANDS:
                        raise ValueError('Unknown executable review continuation command: ' + plan['command'])
                    OPERATIONS[plan['command']](store, plan['payload'],
                        expected_revision=snapshot['revision'], request_id=request_id)
                finally:
                    try:
                        _render_views(store, plan)
                    except BaseException as view_error:
                        _record_omission(plan, 'views', view_error)
            except ResearchError as error:
                if error.code == 'review_chain_stopped':
                    raise
                try:
                    boundary = read_boundary(settle=True)
                except ReviewStopSignal:
                    raise
                except BaseException as read_error:
                    row = dict(plan, request_id=request_id, error=error.as_dict())
                    _record_omission(row, 'boundary', read_error)
                    ending = dict(plan=row, error=error, exit_code=3, current=None, snapshot_failed=True)
                    break
                if boundary is not None:
                    ending = dict(plan=boundary, error=None, exit_code=3, current=None)
                    break
                if error.code == 'review_route_runtime_stale':
                    ending = dict(plan=plan, error=error, exit_code=3, current=snapshot)
                    break
                row = dict(plan, request_id=request_id, error=error.as_dict())
                if ((waiting or plan['state'] == 'resume') and error.code in (
                        'review_worker_reconciliation_required', 'review_invocation_pending')):
                    emit_progress(row)
                    if not waiting:
                        # A capture can precede its live owner's acceptance. The
                        # original request remains the recovery action after release.
                        time.sleep(1)
                    refusals = 0
                    continue
                if error.code in REPLAN_CODES:
                    refusals += 1
                    if refusals >= MAX_CONSECUTIVE_REFUSALS:
                        ending = dict(plan=row, error=error, exit_code=3, current=None)
                        break
                    emit_progress(row)
                    if (error.details or {}).get('request_id') == request_id:
                        repeated_step = snapshot, dict(plan, request_id=request_id)
                    continue
                # Recording can persist an output refusal before raising it. Only
                # that durable transition permits the planner to offer a retry.
                try:
                    current = store.snapshot()
                except BaseException as read_error:
                    _record_omission(row, 'snapshot', read_error)
                    refusals += 1
                    if isinstance(read_error, ResearchError) and read_error.code in REPLAN_CODES and refusals < MAX_CONSECUTIVE_REFUSALS:
                        row['read_error'] = read_error.as_dict()
                        emit_progress(row)
                        continue
                    ending = dict(plan=row, error=error, exit_code=3, current=None, snapshot_failed=True)
                    break
                before_refusals = snapshot['records'].get('review_output_refusal', {})
                new_refusals = current['records'].get('review_output_refusal', {})
                if (new_refusals != before_refusals or error.code == 'review_invocation_pending'):
                    emit_progress(row, usage_snapshot=current)
                    refusals = 0
                    continue
                ending = dict(plan=row, error=error, exit_code=4 if error.code in WAITING_CODES else
                                  0 if error.code == 'problem_already_posted' else 3, current=current)
                break
            # Waiting reconciliations can be successful polls of a still-live
            # owner. Only executable continuation states spend a progress position.
            if not waiting:
                executed[position] = request_id
            refusals = 0
            repeated_step = None
            row = dict(plan, request_id=request_id, revision=snapshot['revision'])
            try:
                ready_step = _read_plan(store, assignment_id)
            except BaseException as read_error:
                _record_omission(row, 'next_plan', read_error)
                # The next loop owns read-error recovery, after this executed step is emitted.
                ready_step = None
            else:
                if 'provider_usage' in ready_step[1]:
                    row['provider_usage'] = ready_step[1]['provider_usage']
            emit_progress(row)
        except _ProgressOutputFailure as failure:
            raise failure.error
        except (ResearchError, ReviewStopSignal) as error:
            if error.code == 'review_chain_stopped':
                ending = dict(plan=_describe_requested_ending(store, error, last_plan, driver),
                              error=None, exit_code=3, current=None)
            else:
                ending = dict(plan=last_plan, error=error, exit_code=3, current=None)
            break
    ending['refused_action'] = ({key: copy.deepcopy(selected_action[key]) for key in ('command', 'state', 'payload')
                                if key in selected_action} if selected_action is not None else None)
    return _end_series(store, output=emit, capacity_records=capacity_records, driver=driver, **ending)


def run_series(store, assignment_id, *, emit=_emit_json):
    """Record the driver lifetime and turn process signals into a requested stop."""
    from .review_stop import (start_driver, end_driver, request_stop, settle_stop,
                              _end_owned_processes, record_signal_boundary, ReviewStopSignal, describe_driver_stop,
                              _select_stop_calls)
    initial = dict(state='stop', chain=None, head_id=assignment_id, progress=None)
    try:
        driver = start_driver(store, assignment_id)
    except ResearchError as error:
        return _end_series(store, initial, error=error, exit_code=3, output=emit)
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
        row = _describe_requested_ending(store, error, initial, driver)
        return _end_series(store, row, exit_code=3, output=emit, driver=driver)
    except BaseException as original_error:
        try:
            end_driver(store, driver['id'], dict(state='ended'))
        except BaseException as cleanup_error:
            raise original_error from cleanup_error
        raise
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
