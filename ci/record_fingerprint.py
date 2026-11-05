#!/usr/bin/env python3
"""Fingerprint a disposable workspace snapshot using one specified harness tree.

The caller must supply a clone, never a live record. All harness imports and
reader functions below were checked at 19291e0 (0.72.4). This script does not
import the candidate's record_check module, classifier or continuation planner.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys


def fingerprint_snapshot(workspace):
    from research_harness.artifacts import ArtifactStore
    from research_harness.errors import ResearchError
    from research_harness.evaluation import Evaluation
    from research_harness import review_protocol as protocol, review_transport as transport
    from research_harness.storage import Store, _RecordHistory, _RecordedSnapshot, _validate

    store = Store(workspace)
    snapshot = store.snapshot()
    records = snapshot['records']
    if records.get('review_output_correction_invocation') and not hasattr(records, 'read_snapshot'):
        history = _RecordHistory()
        with store._connection() as connection:
            _validate(connection, history=history)
        records = _RecordedSnapshot(records, history=history, revision=snapshot['revision'])
    artifacts = Evaluation(records, ArtifactStore(workspace))
    from research_harness.review_state_evaluation import runtime_scope
    with runtime_scope(records, artifacts):
        result = {}
        for identifier, assignment in sorted(records.get('review_assignment', {}).items()):
            entry = {'authentication': {}, 'rebuilt_requests': {}}
            result[identifier] = entry
            try:
                request = protocol.build_assignment_request(records, artifacts, assignment)
                rebuilt = json.dumps(request, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
                entry['assignment_request'] = {'sha256': hashlib.sha256(rebuilt).hexdigest()}
            except (ResearchError, json.JSONDecodeError) as error:
                entry['assignment_request'] = {'error': error.code if isinstance(error, ResearchError)
                                               else 'record_json_invalid'}
            try:
                state = protocol.assignment_state(records, artifacts, identifier)
                entry['assignment_state'] = {
                    'ready': state['ready'], 'attempt_id': state.get('attempt_id'),
                    'refusal_codes': sorted({row['code'] for row in state['obligations']}
                                            | {row['code'] for row in state.get('refused_attempts', [])}),
                }
            except (ResearchError, json.JSONDecodeError) as error:
                entry['assignment_state'] = {'error': error.code if isinstance(error, ResearchError)
                                            else 'record_json_invalid'}
            for attempt_id, attempt in sorted(records.get('review_attempt', {}).items()):
                if attempt['assignment_id'] == identifier:
                    entry['authentication'][attempt_id] = protocol.authenticate_attempt(
                        records, artifacts, assignment, attempt)
            for invocation_id, invocation in sorted(records.get('review_invocation', {}).items()):
                if invocation['assignment_id'] != identifier:
                    continue
                try:
                    raw = artifacts.read(invocation['request'])
                    packet = protocol._read_json(artifacts, assignment['packet'])
                    route = records['review_route'][assignment['route_id']]
                    if assignment.get('staged_review'):
                        from research_harness.review_staged_protocol import build_assigned_request
                        candidates = (build_assigned_request(artifacts, route, assignment, packet),)
                    else:
                        candidates = transport.list_built_requests(
                            route, assignment['prompt']['text'], packet, protocol.read_history(artifacts, assignment))
                    for request in candidates:
                        rebuilt = json.dumps(request, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
                        if raw == rebuilt:
                            entry['rebuilt_requests'][invocation_id] = {'sha256': hashlib.sha256(rebuilt).hexdigest()}
                            break
                    else:
                        entry['rebuilt_requests'][invocation_id] = {'error': 'record_request_changed'}
                except (ResearchError, json.JSONDecodeError) as error:
                    entry['rebuilt_requests'][invocation_id] = {
                        'error': error.code if isinstance(error, ResearchError) else 'record_json_invalid'}
            for key, invocation in sorted(records.get('review_output_correction_invocation', {}).items()):
                if invocation['assignment_id'] != identifier:
                    continue
                from research_harness import review_output_correction as correction
                try:
                    historical = records.read_snapshot(invocation['recorded_revision'] - 1)
                    packet = correction.prepare_packet(historical, Evaluation(historical, artifacts), identifier)
                    route = historical['review_route'][invocation['route_id']]
                    raw = artifacts.read(invocation['request'])
                    for request in transport.list_built_requests(route, correction.PROMPT, packet, []):
                        rebuilt = json.dumps(request, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
                        if raw == rebuilt:
                            value = {'sha256': hashlib.sha256(rebuilt).hexdigest()}
                            break
                    else:
                        value = {'error': 'record_request_changed'}
                    entry['rebuilt_requests']['review_output_correction_invocation:' + key] = value
                except (ResearchError, json.JSONDecodeError) as error:
                    entry['rebuilt_requests']['review_output_correction_invocation:' + key] = {
                        'error': error.code if isinstance(error, ResearchError) else 'record_json_invalid'}
            for kind in ('review_task_dispatch', 'review_execution_plan'):
                for key, record in sorted(records.get(kind, {}).items()):
                    assignment_id = record.get('assignment_id') or next((row['key'] for row in
                        record.get('dependencies', []) if row['kind'] == 'review_assignment'), None)
                    if assignment_id != identifier:
                        continue
                    from research_harness.evidence import digest
                    from research_harness.review_staged_protocol import build_assigned_request
                    try:
                        packet = protocol._read_json(artifacts, assignment['packet'])
                        request = build_assigned_request(artifacts, records['review_route'][assignment['route_id']],
                                                         assignment, packet)
                        rebuilt = json.dumps(request, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
                        if (digest(request) != record['request_digest'] or record.get('request')
                                and artifacts.read(record['request']) != rebuilt):
                            entry['rebuilt_requests'][kind + ':' + key] = {'error': 'record_request_changed'}
                        else:
                            entry['rebuilt_requests'][kind + ':' + key] = {'sha256': hashlib.sha256(rebuilt).hexdigest()}
                    except (ResearchError, json.JSONDecodeError) as error:
                        entry['rebuilt_requests'][kind + ':' + key] = {
                            'error': error.code if isinstance(error, ResearchError) else 'record_json_invalid'}
    return {'assignments': result}


def fingerprint_probe(workspace, identifier):
    """Compare the retained probe request with every supported fit order."""
    from research_harness.artifacts import ArtifactStore
    from research_harness.errors import ResearchError
    from research_harness import review_transport as transport
    from research_harness.storage import Store

    records = Store(workspace).snapshot()['records']
    artifacts = ArtifactStore(workspace)
    invocation = records['review_probe_invocation'][identifier]
    try:
        raw = artifacts.read(invocation['request'])
        route = records['review_route'][invocation['route_id']]
        for request in transport.list_built_requests(
                route, json.loads(raw)['instructions'], invocation['positive_controls'], []):
            rebuilt = json.dumps(request, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
            if raw == rebuilt:
                return {'sha256': hashlib.sha256(rebuilt).hexdigest()}
        return {'error': 'record_request_changed'}
    except (ResearchError, json.JSONDecodeError) as error:
        return {'error': error.code if isinstance(error, ResearchError) else 'record_json_invalid'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--harness', required=True, type=Path)
    parser.add_argument('--workspace', required=True, type=Path, help='A workspace snapshot, never a live record')
    parser.add_argument('--probe-invocation')
    args = parser.parse_args(argv)
    sys.path.insert(0, str(args.harness.resolve(strict=True)))
    workspace = args.workspace.resolve(strict=True)
    result = ({'probe_requests': {args.probe_invocation: fingerprint_probe(workspace, args.probe_invocation)}}
              if args.probe_invocation is not None else fingerprint_snapshot(workspace))
    print(json.dumps(result,
                     sort_keys=True, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
