"""Run the native math controller in its own tests namespace, with a reviewer stub."""

import copy
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'skills/math-solver/harness'))
sys.path.insert(1, str(ROOT / 'tests'))
sys.path.insert(2, str(ROOT))

from tests.test_search_reviewer import NativeReviewerTests
from tests.test_search_evidence import EvidenceTests
from tests.native_reviewer_support import mutate, response
from codex_route_fixtures import record_codex_route, invoke_codex
from research_harness import review_parts as parts, review_protocol as protocol
from review_parts_fixtures import make_output
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.storage import Store
from research_harness.review_continuation import plan_next
from research_harness.review_series import run_series


def run_journey():
    case = NativeReviewerTests()
    case.setUp()
    try:
        case.artifact = lambda path, text: EvidenceTests.artifact(case, path, text)
        spec = EvidenceTests.external_checkpoint(case)
        case.controller.command('checkpoint', spec, 2, 'checkpoint')
        checkpoint = case.controller.status()['checkpoints']['checkpoint-000001']
        output = EvidenceTests.result_review(case, checkpoint, checkpoint['claim'])
        output['reviewer'].update(source='host', attestation_id='review-assignment:old')
        exported = case.controller.command('review-packet', dict(kind='result', subject=checkpoint,
            claim_digest=output['claim_digest'], reviewer=output['reviewer'], inputs=[]), None, None)
        case.store = Store(exported['workspace'])
        case.artifacts = ArtifactStore(case.store.root)
        record_codex_route(case, 'serving', output_tokens=128000)
        mutate(case.store, protocol.record_route, dict(id='https', adapter='openai_responses_v1',
            model='fixture-model', endpoint='https://example.test/responses',
            configuration=dict(max_output_tokens=10000, timeout_seconds=5)))
        def respond_to_probe(route, request, credential):
            from research_harness.review_excerpts import read_sent_packet
            packet = read_sent_packet(request)
            return response(dict(allowed_control=packet['allowed_control'],
                evidence_control=packet['evidence_control'], excluded_controls=[]))
        with patch.object(protocol.transport, 'send_request', side_effect=respond_to_probe):
            mutate(case.store, protocol.probe_route, dict(id='https-probe', route_id='https'), credential='fixture')
        mutate(case.store, protocol.record_assignment, dict(id='old', role='native_math', dossier_id=None,
            reviewer_id=output['reviewer']['actor_id'], author_id='author', route_id='https',
            context=dict(native_packet=exported['native_packet'])))
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'fixture'}), patch.object(
                protocol.transport, 'send_request', side_effect=ResearchError(
                    'review_response_too_large', 'The provider response exceeds 16 MiB')) as sender:
            run_series(case.store, 'old', emit=lambda row: None)
        assert sender.call_count == 2
        plan = plan_next(case.store.snapshot()['records'], case.artifacts, 'old')
        assert (plan['state'], plan['code']) == ('stop', 'review_response_too_large')
        steps = plan['details']['decision']['then'][0]['steps']
        assert [step['id'] for step in steps] == [5]
        # D2 row 5: the actual review-packet command records the new attestation.
        fresh_output = copy.deepcopy(output)
        fresh_output['reviewer']['attestation_id'] = 'review-assignment:new'
        fresh = case.controller.command('review-packet', dict(steps[0]['payload'],
            reviewer=fresh_output['reviewer'], inputs=[]), None, None)
        payload = dict(steps[0]['then'][0]['payload'], id='new', route_id='serving',
            reviewer_id=fresh_output['reviewer']['actor_id'], context=dict(native_packet=fresh['native_packet']))
        assignment = mutate(case.store, protocol.record_assignment, payload)['result']
        for number in range(40):
            packet = protocol._read_json(case.artifacts, assignment['packet'])
            kind = parts.find_kind(packet)
            invoke_codex(case, assignment['id'], fresh_output if kind in (None, 'final') else make_output(packet))
            if kind in (None, 'final'):
                break
            following = plan_next(case.store.snapshot()['records'], case.artifacts, assignment['id'])
            assert following['state'] in ('assign', 'continue'), following
            assignment = mutate(case.store, protocol.record_assignment, following['payload'])['result']
        else:
            raise AssertionError('The reattested native review did not finish its series')
        case.controller.command('accept', dict(obligation_id='obligation-000001', outcome='proof',
            dependency_ids=[], route_bindings=[], review=fresh_output, inputs=[]), 3, 'accept', checkpoint['id'])
        assert case.controller.status()['acceptances']['acceptance-000001']['status'] == 'accepted'
        print('math response limit: rows 5 and assignment, observed review, recorded acceptance')
    finally:
        case.doCleanups()


if __name__ == '__main__':
    run_journey()
