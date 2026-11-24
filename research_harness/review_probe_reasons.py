"""The reasons why a probe record does not verify its route record, for the operator.

review_protocol._judge_route_probe alone decides the state of a route record. This module names the failed checks
from the same stored facts. A stored fact that cannot be read gives no code of its own.
"""

import json

from .errors import ResearchError


def _read_stored(artifacts, reference):
    """The stored JSON value of an artifact reference, or None when the reference is absent or cannot be read."""
    from .review_protocol import _read_json
    if reference is None:
        return None
    try:
        return _read_json(artifacts, reference)
    except (ResearchError, ValueError, TypeError, KeyError):
        return None


def list_unverified_reasons(artifacts, route, probe):
    """The codes of the checks that a probe record fails, in a fixed order, or ['unclassified']."""
    from .review_codex_invocation import KEY, read_turn_context
    from .review_codex_transport import read_route_effort
    reasons = []
    completed = probe.get('status') == 'completed'
    if not completed:
        reasons.append('call_incomplete')
    response = _read_stored(artifacts, probe.get('response'))
    response = response if isinstance(response, dict) else {}
    context = response.get('context_evidence')
    issues = context.get('issues') if isinstance(context, dict) else None
    if isinstance(issues, list):
        reasons.extend('context:' + str(issue) for issue in issues)
    capture = response.get(KEY)
    if isinstance(capture, dict):
        if 'error' in capture:
            reasons.append('context:rollout_unavailable')
        events = capture.get('native_rollout')
        turn = (read_turn_context(events) if isinstance(events, list)
                and all(isinstance(event, dict) for event in events) else None)
        if isinstance(turn, dict) and turn.get('model') != route['model']:
            reasons.append('context:turn_model')
        if isinstance(turn, dict) and turn.get('effort') != read_route_effort(route):
            reasons.append('context:turn_effort')
    request = _read_stored(artifacts, probe.get('request'))
    output = _read_stored(artifacts, probe.get('output'))
    controls = _read_stored(artifacts, probe.get('controls'))
    if isinstance(controls, dict) and any(str(token) in json.dumps(value) for value in (request, output)
                                          if value is not None for token in controls.values()):
        reasons.append('excluded_control')
    positive = probe.get('positive_controls') or {}
    if completed and (not isinstance(output, dict) or output.get('excluded_controls') != []
                      or any(output.get(key) != positive.get(key) for key in ('allowed_control', 'evidence_control'))):
        reasons.append('control_mismatch')
    return reasons or ['unclassified']
