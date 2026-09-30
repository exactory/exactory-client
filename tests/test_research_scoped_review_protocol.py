"""Scoped result assignments preserve the checked scientific byte boundary."""

import copy
import json

from source_limited_fixtures import SourceLimitedCase
from test_research_scoped_decisions import ScopedDecisionTests
from research_harness import review_protocol


class ScopedPacketTests(SourceLimitedCase):
    module = ScopedDecisionTests.module
    put = ScopedDecisionTests.put
    dossier = ScopedDecisionTests.dossier
    provider_response = ScopedDecisionTests.provider_response
    route = ScopedDecisionTests.route
    review_response = ScopedDecisionTests.review_response
    record_response = ScopedDecisionTests.record_response
    intent = ScopedDecisionTests.intent
    prepare_scoped_strategy = ScopedDecisionTests.prepare_scoped_strategy

    def packet(self):
        return review_protocol.build_packet(self.store.snapshot()['records'], self.artifacts,
                                            'dossier-1', 'result', 'reviewer-a')

    def test_scoped_result_uses_exact_target_and_checked_derivatives(self):
        self.prepare_scoped_strategy()
        report = self.manuscript_readiness()
        packet = self.packet()
        self.assertEqual(packet['support_contract']['candidate_digest'], report['candidate_digest'])
        self.assertEqual(packet['support_contract']['target'], {
            'kind': 'source_limited_manuscript', 'contract_id': report['contract']['id'],
            'scientific_target_digest': report['scientific_target_digest']})
        self.assertIn('source_limits', packet['support_contract']['required_checks'])
        self.assertIn(self.debt, packet['readiness']['remaining_objective_obligations'])
        self.assertFalse(report['objective_complete'])
        content = json.dumps(packet)
        for excluded in ('PRIVATE', 'scope-preparer', 'scope-reviewer', 'scientific_preparers'):
            self.assertNotIn(excluded, content)
        self.assertIn('original_sha256', content)
        original = self.execution_payload['outputs'][0]['artifact']
        self.assertNotIn(original['sha256'], {r['sha256'] for r in review_protocol._refs(packet)})
        self.assertIn(original['sha256'], json.dumps(packet['support_contract']['evidence']))

    def test_scoped_result_cannot_fall_back_to_raw_authored_bytes(self):
        self.prepare_scoped_strategy()
        payload = copy.deepcopy(self.manuscript_readiness()['contract']['payload'])
        payload.update(id='scope-without-delivery', scientific_delivery=[])
        self.record_scope(payload)
        self.assert_error('private_mixed_artifact_required', self.packet)

    def test_scoped_manuscript_assignment_keeps_exact_files_and_private_context_excluded(self):
        self.prepare_delivery()
        bundle = self.scoped_manuscript()
        packet = review_protocol.build_packet(self.store.snapshot()['records'], self.artifacts,
                                               None, 'manuscript', 'paper-reviewer', bundle=bundle)
        self.assertEqual(packet['paper']['bundle_digest'], bundle['digest'])
        self.assertEqual(packet['paper']['files']['pdf'], bundle['files']['pdf'])
        content = json.dumps(packet)
        for excluded in ('PRIVATE', 'scope-preparer', 'scope-reviewer', 'scientific_preparers', 'independence_basis'):
            self.assertNotIn(excluded, content)
        self.assertIn('external comparison remains unavailable', content)
        self.assertIn('original_sha256', content)
