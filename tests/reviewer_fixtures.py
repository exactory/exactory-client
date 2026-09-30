"""Observed review receipts with only the external provider replaced by a fixture."""

import json
from unittest.mock import patch

from research_harness import review_protocol
from research_harness.operations import normalized_text


def manuscript_assignment(case, bundle, assessor, core, prediction, reasons):
    if 'candidate' not in bundle or normalized_text(assessor) in {
            normalized_text(author) for author in bundle['candidate']['authors']}:
        return None
    records = case.store.snapshot()['records']
    for saved in records.get('review_assignment', {}).values():
        if (saved['role'] == 'manuscript' and normalized_text(saved['reviewer_id']) == normalized_text(assessor)
                and saved['context'].get('bundle', {}).get('digest') == bundle['digest']):
            return saved['id']
    route_id = 'fixture-paper-route'
    if route_id not in records.get('review_route', {}):
        case.mutate(review_protocol.record_route, {'id':route_id,'adapter':'openai_responses_v1',
            'model':'fixture-model','endpoint':'https://api.openai.com/v1/responses',
            'configuration':{'max_output_tokens':10000,'timeout_seconds':5}})
        def positive(route, request, credential):
            packet = json.loads(request['input'][0]['content'])
            return response({'allowed_control':packet['allowed_control'],
                             'evidence_control':packet['evidence_control'],'excluded_controls':[]})
        with patch('research_harness.review_transport.send_request', side_effect=positive):
            case.mutate(review_protocol.probe_route, {'id':'fixture-paper-probe','route_id':route_id}, credential='fixture')
    identifier = 'paper-' + str(case.store.revision) + '-' + normalized_text(assessor)
    author = bundle['candidate']['authors'][0] if bundle['candidate']['authors'] else 'fixture-author'
    case.mutate(review_protocol.record_assignment, {'id':identifier,'route_id':route_id,'dossier_id':None,
        'role':'manuscript','reviewer_id':assessor,'author_id':author,'context':{'bundle':bundle}})
    with patch('research_harness.review_transport.send_request', return_value=response({
            'review':core, 'prediction':prediction, 'reasons':reasons})):
        case.mutate(review_protocol.invoke_assignment, {'id':identifier+'-attempt','assignment_id':identifier}, credential='fixture')
    return identifier


def response(output):
    return {'id':'fixture-provider-response','model':'fixture-model','status':'completed',
        'output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':json.dumps(output)}]}],
        'usage':{'input_tokens':37,'output_tokens':12}}


def verdict_assignment(case, payload):
    """Observe the exact verdict, replacing only the external model response."""
    route_id = 'fixture-verdict-route'
    if route_id not in case.store.snapshot()['records'].get('review_route', {}):
        case.mutate(review_protocol.record_route, {'id': route_id, 'adapter': 'openai_responses_v1',
            'model': 'fixture-model', 'endpoint': 'https://api.openai.com/v1/responses',
            'configuration': {'max_output_tokens': 10000, 'timeout_seconds': 5}})
        def positive(route, request, credential):
            packet = json.loads(request['input'][0]['content'])
            return response({'allowed_control': packet['allowed_control'],
                             'evidence_control': packet['evidence_control'], 'excluded_controls': []})
        with patch('research_harness.review_transport.send_request', side_effect=positive):
            case.mutate(review_protocol.probe_route, {'id': 'fixture-verdict-probe',
                'route_id': route_id}, credential='fixture')
    identifier = 'verifier-' + str(case.store.revision)
    case.mutate(review_protocol.record_assignment, {'id': identifier, 'route_id': route_id,
        'dossier_id': None, 'role': 'verification', 'reviewer_id': payload['assessment']['assessor'],
        'author_id': 'external-paper-author', 'context': {'verification_task_id': payload['task_digest']}})
    observed = {'verdict': json.loads(case.artifacts.read(payload['body'])),
                'checks': payload['assessment']['checks']}
    with patch('research_harness.review_transport.send_request', return_value=response(observed)):
        case.mutate(review_protocol.invoke_assignment, {'id': identifier + '-attempt',
            'assignment_id': identifier}, credential='fixture')
    return identifier
