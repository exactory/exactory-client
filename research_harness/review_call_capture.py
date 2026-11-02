"""Durable observations of owned ordinary calls, separate from scientific receipts."""

import json
import os

from .errors import ResearchError
from .evidence import digest
from .finishing import retry_store_write
from .operations import immutable_record
from .review_task_execution import _get, _mutate_current, _TransactionRecords, identify_call, read_call_kinds
from .storage import _canonical


PROTOCOL = 'review-call-observation-v1'


def _check_bindings(records, artifacts, captured, operation, payload, request_id):
    from .review_protocol import collect_references, _read_json
    identifier = identify_call(operation, payload['id'])
    invocation_kind, _ = read_call_kinds(operation)
    invocation = _get(records, invocation_kind, payload['id'])
    membership = _get(records, 'review_pool_membership', identifier)
    route_binding = captured['route']
    if (captured['protocol'] != PROTOCOL or captured['origin'] != 'instrumented'
            or captured['id'] != identifier or captured['reservation_id'] != identifier
            or captured['operation'] != operation or captured['invocation_id'] != payload['id']
            or captured['request_id'] != request_id or captured['payload_digest'] != digest(payload)
            or captured['invocation'] != dict(kind=invocation_kind, key=payload['id'], digest=digest(invocation))
            or route_binding['kind'] != 'review_route' or route_binding['key'] != invocation['route_id']
            or any(value is not None and value != route_binding['digest'] for value in (
                invocation.get('route_digest'), captured['observation'].get('route_digest')))
            or captured['request'] != invocation['request']
            or captured['request_digest'] != digest(_read_json(artifacts, invocation['request']))
            or captured['owner'] != membership['owner'] or captured['token'] != membership['token']
            or membership['id'] != identifier or membership['route_id'] != invocation['route_id']
            or membership['operation'] != operation or membership['invocation_id'] != payload['id']):
        raise ResearchError('review_invocation_unverified', 'The committed call observation must bind its original invocation')
    for reference in collect_references([captured['observation'], captured['retained_evidence']]):
        artifacts.read(reference)
    return invocation


def capture_call(store, operation, payload, invocation, route, observation, *, request_id, _authority=None):
    """Retain native evidence once, then commit only through the original live call authority."""
    from .review_worker import _CallAuthority
    from .review_protocol import _put_json, _read_json
    if not isinstance(_authority, _CallAuthority):
        raise ResearchError('review_instrumented_capture_required', 'Only the owned live call captures its observation')
    # Own exact JSON values before either retention or write retries can run.
    payload, invocation, route, observation = json.loads(_canonical([payload, invocation, route, observation]))
    artifacts = store.build_artifact_store()
    retained = {}
    if operation == 'review.output_correction' and route['adapter'] == 'codex_cli_v1' and observation['status'] == 'completed':
        try:
            from . import review_codex_transport as codex
            response = _read_json(artifacts, observation['response'])
            events = codex._rollout(response['id'], codex.clean_environment(os.environ))
            retained['native_rollout'] = _put_json(artifacts, events)
        except (ResearchError, ValueError, TypeError, KeyError, OSError) as error:
            retained['native_rollout_error'] = dict(code=getattr(error, 'code', 'review_correction_invalid'), reason=str(error))
    identifier = identify_call(operation, invocation['id'])
    invocation_kind, _ = read_call_kinds(operation)
    captured = dict(id=identifier, protocol=PROTOCOL, origin='instrumented', operation=operation,
        invocation_id=invocation['id'], request_id=request_id, payload_digest=digest(payload),
        invocation=dict(kind=invocation_kind, key=invocation['id'], digest=digest(invocation)),
        route=dict(kind='review_route', key=route['id'], digest=digest(route)),
        request=invocation['request'], request_digest=digest(_read_json(artifacts, invocation['request'])),
        reservation_id=identifier, owner=_authority.identity, token=_authority.token,
        observation=observation, retained_evidence=retained)

    def apply(tx, value, revision):
        records = _TransactionRecords(tx)
        membership = _get(records, 'review_pool_membership', identifier)
        _authority.verify(store, membership)
        _check_bindings(records, artifacts, value, operation, payload, request_id)
        result = dict(value, recorded_revision=revision)
        tx.put(*immutable_record(records, 'review_call_observation', identifier, result))
        return result

    suffix = 0
    while True:
        capture_request_id = request_id + ':capture' + (':' + str(suffix) if suffix else '')
        try:
            return retry_store_write(lambda: _mutate_current(store, 'review.call_capture', captured, apply,
                request_id=capture_request_id), attempts=600, seconds=600.0)
        except ResearchError as error:
            if error.code != 'request_id_conflict':
                raise
            # Receipt names are public; a conflicting name must not discard an already observed call.
            # The serialized mutation detects both earlier occupants and concurrent writers.
            suffix += 1


def read_call_observation(records, artifacts, operation, payload, request_id):
    """Authenticate a committed capture inside the finishing preparation's read set."""
    captured = _get(records, 'review_call_observation', identify_call(operation, payload['id']))
    invocation = _check_bindings(records, artifacts, captured, operation, payload, request_id)
    return invocation, dict(captured['observation'], **captured['retained_evidence'])
