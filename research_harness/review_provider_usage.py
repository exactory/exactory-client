"""Read retained subscription windows and estimate chain usage without changing evidence."""

from datetime import datetime, timezone
import json
import math

from .errors import ResearchError
from . import review_protocol as protocol


_USAGE_WARNING = ('The reviewer turns that this chain still plans are estimated to need more of this usage window '
                  'than is left before its reset. The estimate is advisory. It changes no plan, and usage figures '
                  'do not decide the outcome of a call. A call is the outcome usage_limit only when the provider '
                  'answers it with its limit message. review-series then waits until the reset that the answer '
                  'states, or by the provider schedule, and stops when the failures that the schedule or the turn '
                  'position allows are used up. If a wait ends more than 30 minutes ahead, review-series exits '
                  'with status 4. Run it again at not_before. Report the estimate in your summary. The details '
                  'give the figures and their ground.')
_USAGE_GROUND = ('utilization_per_call is the mean increase of the utilization from one call to the next. It is taken '
                 'over the latest consecutive retained responses of calls on Claude routes of this record that '
                 'report this window with one usable reset time and a utilization that does not decrease. A '
                 'retained response that does not report the window, another reset time or a lower utilization '
                 'starts a new sample. A call without a retained response is left out and ends no sample. Other '
                 'use of the account between those calls is part of that increase. The estimate counts one call '
                 'for each planned turn. It does not count a run that is repeated after a failure or a turn that '
                 'a passage request adds.')


def _list_calls(records):
    calls = {}
    for captured in records.get('review_call_observation', {}).values():
        observation = captured['observation']
        if observation.get('response') is not None:
            key = (captured['operation'], captured['invocation_id'])
            calls[key] = dict(observation, id=captured['id'], recorded_revision=captured['recorded_revision'],
                              route_id=captured.get('route', {}).get('key'))
    for captured in records.get('review_observation', {}).values():
        observation = captured['observation']
        if observation.get('response') is not None:
            key = ('reservation', captured['reservation_id'])
            execution_plan = records.get('review_execution_plan', {}).get(captured.get('plan_id'), {})
            calls[key] = dict(observation, id=captured['id'], recorded_revision=captured['recorded_revision'],
                              route_id=execution_plan.get('route_id'))
    for kind, operation in (('review_attempt', 'review.invoke'), ('review_route_probe', 'review.route_probe'),
                            ('review_output_correction', 'review.output_correction')):
        for attempt in records.get(kind, {}).values():
            if attempt.get('response') is None:
                continue
            reservation = attempt.get('reservation_id')
            key = (('reservation', reservation) if reservation in records.get('review_observation', {})
                   else (operation, attempt.get('invocation_id', attempt['id'])))
            if key in calls:
                # Scientific acceptance can be delayed past another call's capture.
                calls[key]['id'] = attempt['id']
            else:
                calls[key] = attempt
    return sorted(calls.values(), key=lambda row: row['recorded_revision'])


def _read_window(event):
    info = event.get('rate_limit_info')
    if not isinstance(info, dict):
        return None
    utilization, reset, window = info.get('utilization'), info.get('resetsAt'), info.get('rateLimitType')
    if (type(utilization) not in (float, int) or not 0 <= utilization <= 1
            or type(utilization) is float and not math.isfinite(utilization)
            or not isinstance(window, str) or not window):
        return None
    reset_at = None
    if type(reset) is int or type(reset) is float and math.isfinite(reset):
        try:
            reset_at = datetime.fromtimestamp(reset, timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            pass
    return dict(window=window, utilization=utilization, reset_at=reset_at, status=info.get('status'))


def _count_remaining_turns(records, artifacts, plan):
    assignment = records.get('review_assignment', {}).get(plan.get('head_id'))
    if plan['state'] in ('done', 'stop', 'handoff', 'record'):
        return 0
    if assignment is None or assignment.get('staged_review'):
        return None
    packet = protocol._read_json(artifacts, assignment['packet'])
    from . import openness_reading, review_excerpts, review_parts
    if openness_reading.TURN_KEY in packet:
        reading = packet[openness_reading.TURN_KEY]['reading_plan']
        units = list(reading['remaining'])
        if plan['state'] not in ('continue', 'record'):
            units = reading['delivered_now'] + units
        turns, characters = 0, 0
        for unit in units:
            if characters and characters + unit['characters'] > review_excerpts.TURN_FULLTEXT_CHARACTERS:
                turns += 1
                characters = 0
            characters += unit['characters']
        return turns + bool(characters) + 1  # Include the final finding.
    if review_parts.KEY in packet:
        # Later combine/check tasks depend on reviewer discoveries, so phase
        # estimates cannot count the rest of this chain.
        return 1 if review_parts.find_kind(packet) == 'final' else None
    return 1


def describe_usage(records, artifacts, *, plan=None):
    """Show each latest window and a forecast grounded only in consecutive retained calls.

    Missing events, a reset change or a utilization decrease break the sample. A
    flat sample does not establish a zero-cost call. All figures are advisory.
    """
    windows, samples, previous = {}, {}, {}
    for call in _list_calls(records):
        assignment = records.get('review_assignment', {}).get(call.get('assignment_id'), {})
        route = records.get('review_route', {}).get(call.get('route_id') or assignment.get('route_id'), {})
        if route and route['adapter'] != 'claude_cli_v1':
            continue
        try:
            response = protocol._read_json(artifacts, call['response'])
        except (ResearchError, json.JSONDecodeError):
            previous = {}
            continue
        # Other transports do not use the Claude subscription allowance.
        if response.get('context_evidence', {}).get('runtime', {}).get('adapter', 'claude_cli_v1') != 'claude_cli_v1':
            continue
        current = {}
        for entry in response.get('execution', {}).get('rate_limit_events', []):
            window = _read_window(entry['event'])
            if window is not None:
                current[window['window']] = window
        for name, window in current.items():
            prior = previous.get(name)
            if (prior is not None and window['reset_at'] is not None
                    and prior['reset_at'] == window['reset_at']
                    and window['utilization'] >= prior['utilization']):
                increase, intervals = samples[name]
                samples[name] = increase + window['utilization'] - prior['utilization'], intervals + 1
            else:
                samples[name] = 0, 0
            increase, intervals = samples[name]
            windows[name] = dict(window, attempt_id=call['id'], observed_at=call.get('observed_at'),
                sample_calls=intervals + 1, sample_intervals=intervals,
                utilization_per_call=increase / intervals if intervals and increase > 0 else None)
        previous = current
    result = dict(windows=[windows[key] for key in sorted(windows)], warnings=[])
    if plan is None:
        return result
    turns = _count_remaining_turns(records, artifacts, plan)
    result['planned_remaining_turns'] = turns
    assignment = records.get('review_assignment', {}).get(plan.get('head_id'), {})
    route = records.get('review_route', {}).get(assignment.get('route_id'), {})
    if turns is None or route.get('adapter', 'claude_cli_v1') != 'claude_cli_v1':
        return result
    for window in result['windows']:
        per_call = window['utilization_per_call']
        if (per_call is None or window['reset_at'] is None
                or datetime.fromisoformat(window['reset_at']) <= datetime.now(timezone.utc)):
            continue
        estimate, remaining = turns * per_call, max(0, 1 - window['utilization'])
        if estimate > remaining and not math.isclose(estimate, remaining, rel_tol=1e-9, abs_tol=1e-12):
            details = dict(window, planned_remaining_turns=turns, estimated_utilization=estimate,
                           remaining_utilization=remaining, estimated_remaining_calls=remaining / per_call,
                           ground=_USAGE_GROUND)
            result['warnings'].append(dict(code='review_usage_window_insufficient',
                                           message=_USAGE_WARNING, details=details))
    return result
