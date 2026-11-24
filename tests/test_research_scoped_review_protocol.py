"""Scoped result assignments preserve the checked scientific byte boundary."""

from unittest.mock import patch
import copy
import json

from source_limited_fixtures import SourceLimitedCase
# The module, not its test class: discovery collects each test class that a test module holds by name.
import test_research_scoped_decisions as decision_fixtures
from research_harness import review_protocol


class ScopedPacketTests(SourceLimitedCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    module = decision_fixtures.ScopedDecisionTests.module
    put = decision_fixtures.ScopedDecisionTests.put
    dossier = decision_fixtures.ScopedDecisionTests.dossier
    provider_response = decision_fixtures.ScopedDecisionTests.provider_response
    route = decision_fixtures.ScopedDecisionTests.route
    review_response = decision_fixtures.ScopedDecisionTests.review_response
    record_response = decision_fixtures.ScopedDecisionTests.record_response
    intent = decision_fixtures.ScopedDecisionTests.intent
    prepare_scoped_strategy = decision_fixtures.ScopedDecisionTests.prepare_scoped_strategy

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
        self.assertNotIn(original['sha256'], {r['sha256'] for r in review_protocol.collect_references(packet)})
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
