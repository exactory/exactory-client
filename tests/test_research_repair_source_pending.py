"""Context repair cannot discard the predecessor's actual source requests."""

from strategy_fixtures import StrategyCase
import test_research_bar_sources as source_fixtures
import test_research_context_repair_integration as repair_fixtures
from research_harness import research_decisions, review_protocol


class RepairSourcePendingTests(StrategyCase):
    metadata = source_fixtures.BarSourceTests.metadata
    assign = source_fixtures.BarSourceTests.assign
    packet = source_fixtures.BarSourceTests.packet
    invoke = source_fixtures.BarSourceTests.invoke
    request = source_fixtures.BarSourceTests.request
    expose = repair_fixtures.ContextRepairIntegrationTests.expose
    probe_repair = repair_fixtures.ContextRepairIntegrationTests.probe_repair

    def setUp(self):
        super().setUp()
        self.sequence = 0
        self.setup_dossier()
        self.route()

    def repair(self, predecessor='a'):
        self.expose(assignment=predecessor)
        self.probe_repair()
        return self.assign('repair', 'repair-reviewer', context_repair={
            'assignment_id': predecessor, 'event_ids': ['exposure'], 'probe_id': 'repair-probe',
            'reason': 'Reassess the exact observed source turn after context contamination.',
            'evidence': [self.evidence]})

    def final(self, predecessor='a'):
        output = self.review_response('bar')
        output['reassessment'] = {'assignment_id': predecessor, 'findings': []}
        return output

    def record_final(self, output, assignment='repair'):
        return self.mutate(research_decisions.record_value_review,
            dict(output, id='final-repair', dossier_id='dossier-1', assignment_id=assignment))

    def test_repair_preserves_newly_observed_unavailable_request_and_blocks_final(self):
        needed = {'id': 'fulltext', 'kind': 'source', 'version_id': 'arxiv:2601.00999v1', 'depth': 'fulltext'}
        self.assign()
        observed = self.request([needed])
        self.repair()
        self.assertEqual(self.packet('repair')['pending_source_requests'], [needed])
        self.assertEqual(self.packet('repair')['scientific_history'][-1]['observed_output'], observed)
        output = self.final()
        self.invoke(output, 'repair')
        state = review_protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, 'repair')
        self.assertEqual(state['source_request_status'], 'pending')
        self.error('review_sources_pending', lambda: self.record_final(output))

    def test_repair_keeps_inherited_and_new_requests_through_another_source_turn(self):
        missing = {'id': 'missing', 'kind': 'source', 'version_id': 'arxiv:2601.00999v1', 'depth': 'fulltext'}
        page = {'id': 'page', 'kind': 'inventory_page', 'page': 0}
        self.assign()
        self.request([missing])
        self.assign('child', prior_assignment_id='a')
        self.request([page], 'child')
        self.repair('child')
        self.assertEqual(self.packet('repair')['pending_source_requests'], [missing, page])
        self.request([{'id': 'query', 'kind': 'inventory_query', 'query': 'absent', 'page': 0}], 'repair')
        self.assign('repaired-child', 'repair-reviewer', prior_assignment_id='repair')
        packet = self.packet('repaired-child')
        self.assertEqual(packet['pending_source_requests'], [missing])
        self.assertEqual({d['request']['id'] for d in packet['source_deliveries']}, {'missing', 'page', 'query'})
        output = self.final('child')
        self.invoke(output, 'repaired-child')
        self.error('review_sources_pending', lambda: self.record_final(output, 'repaired-child'))

    def test_repair_request_can_settle_only_after_exact_native_delivery(self):
        source = self.metadata(1, abstract='Actual requested source bytes delivered after context repair.')
        needed = {'id': 'abstract', 'kind': 'source', 'version_id': source, 'depth': 'abstract'}
        self.assign()
        self.request([needed])
        self.repair()
        self.assertEqual(self.packet('repair')['pending_source_requests'], [needed])
        self.request([{'id': 'page', 'kind': 'inventory_page', 'page': 0}], 'repair')
        self.assign('fulfilled', 'repair-reviewer', prior_assignment_id='repair')
        packet = self.packet('fulfilled')
        self.assertEqual(packet['pending_source_requests'], [])
        delivery = next(d for d in packet['source_deliveries'] if d['request']['id'] == 'abstract')
        original = self.store.snapshot()['records']['work'][source]['abstracts'][0]['artifact']
        self.assertEqual(delivery['evidence'], [original])
        self.assertEqual(delivery['status'], 'delivered')
        output = self.final()
        self.invoke(output, 'fulfilled')
        self.record_final(output, 'fulfilled')
        self.assertIn('final-repair', self.store.snapshot()['records']['value_review'])
