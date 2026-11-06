"""Re-admit retained demonstration inputs through the candidate operations."""

import copy
import json

from research_harness import cli
from research_harness.errors import ResearchError
from research_harness.problem_openness import export_openness


def prepare_assignment(store, case):
    if case == 'quantum-focused-adjudication':
        payload_file = store.root / 'payloads/assignment-adjudication-primary-sources.json'
    elif case == 'bunkbed-openness-v5':
        payload_file = store.root / 'payloads/assign-openness-5.json'
    else:
        raise ResearchError('measurement_assignment_required',
                            'Supply --assignment for this measurement case.')
    payload = copy.deepcopy(json.loads(payload_file.read_text(encoding='utf-8')))
    payload['id'] = 'measurement-' + case
    if case == 'bunkbed-openness-v5':
        spec_file = store.root / 'notes/openness-spec-5.json'
        spec = json.loads(spec_file.read_text(encoding='utf-8'))
        spec['assignment_id'] = payload['id']
        exported = export_openness(store, spec, store.root / 'measurement-openness-packet')
        payload['context'] = dict(native_packet=exported['native_packet'])
    return cli.OPERATIONS['review-assignment'](store, payload,
        expected_revision=store.revision, request_id='measurement-prepare:' + case)['result']['id']
