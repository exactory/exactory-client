"""Observed source batches retain authoritative pending requests through native gates."""

import json
from unittest.mock import patch

from research_harness import research_decisions, review_protocol, review_transport
from research_harness import review_supplied_evidence
from research_harness.review_codex_transport import packet_text
from strategy_fixtures import StrategyCase
import test_research_bar_sources as sources
import test_research_context_repair_integration as repairs


class SourceCapacityPendingTests(StrategyCase):
    metadata = sources.BarSourceTests.metadata
    capture = sources.BarSourceTests.capture
    assign = sources.BarSourceTests.assign
    packet = sources.BarSourceTests.packet
    invoke = sources.BarSourceTests.invoke
    request = sources.BarSourceTests.request
    expose = repairs.ContextRepairIntegrationTests.expose
    probe_repair = repairs.ContextRepairIntegrationTests.probe_repair

    def setUp(self):
        super().setUp()
        self.sequence = 0
        self.setup_dossier()
        self.route()
        self.versions, self.captures = [], []
        for index in range(1, 4):
            version = self.metadata(index)
            body = ('Exact complete mathematical source %d with its proof. ' % index) * 1100
            self.versions.append(version)
            self.captures.append(self.capture(version, pdf_text=body + '\f'))
        original_request = review_transport.request_body

        def local_request(route, prompt, packet, history=()):
            local = dict(route, adapter='codex_cli_v1', endpoint='codex://local')
            return original_request(local, prompt, packet, history)

        # Exercise native records and their usual observed Responses fixture,
        # with exactly the real local input projection at a small test boundary.
        self.patch = patch.object(review_transport, 'request_body', side_effect=local_request)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.limit = patch.object(review_transport, 'CODEX_INPUT_LIMIT_CHARS', 105000)
        self.limit.start()
        self.addCleanup(self.limit.stop)
        self.assign()
        self.requests = [{'id': 'source-%d' % index, 'kind': 'source', 'version_id': version, 'depth': 'fulltext'}
                         for index, version in enumerate(self.versions)]
        self.request(self.requests)
        with review_transport.text_only_delivery(True):
            self.assign('batch', prior_assignment_id='a')

    def observed_batch(self, output=None):
        output = output or self.review_response('bar')
        self.invoke(output, 'batch')
        records = self.store.snapshot()['records']
        attempt = records['review_attempt']['attempt-batch']
        request = json.loads(self.artifacts.read(attempt['request']))
        self.assertLessEqual(len(packet_text(request)), 105000)
        packet = json.loads(request['input'][-1]['content'].split('\n', 1)[1])
        self.assertTrue(packet['pending_source_requests'])
        self.assertEqual(self.packet('batch')['pending_source_requests'], [])
        return output, packet

    def record_final(self, output, assignment='batch'):
        return self.mutate(research_decisions.record_value_review,
            dict(output, id='final', dossier_id='dossier-1', assignment_id=assignment))

    def test_premature_final_with_no_repeated_requests_cannot_settle_capacity_deferrals(self):
        output, sent = self.observed_batch()
        state = review_protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, 'batch')
        self.assertTrue(state['ready'])
        self.assertEqual(state['pending_source_requests'], sent['pending_source_requests'])
        self.assertEqual(state['source_request_status'], 'pending')
        self.error('review_sources_pending', lambda: self.record_final(output))
        with review_transport.text_only_delivery(True):
            continued = self.assign('next-batch', prior_assignment_id='batch')
        self.assertEqual(continued['session_id'], 'a')
        self.assertEqual([item['request'] for item in self.packet('next-batch')['source_deliveries']],
                         sent['pending_source_requests'])

    def test_withheld_artifact_has_no_supplied_evidence_credit(self):
        _, sent = self.observed_batch()
        records = self.store.snapshot()['records']
        references, _ = review_supplied_evidence.supplied_context(records, self.artifacts, ['batch'], frozen=True)
        missing = {entry['artifact']['sha256'] for entry in sent['evidence']
                   if entry.get('delivery') == 'body-deferred-source-capacity'}
        self.assertTrue(missing)
        self.assertFalse(missing & set(references))

    def test_context_repair_keeps_actual_pending_even_when_output_omits_it(self):
        output, sent = self.observed_batch()
        self.expose(assignment='batch')
        self.probe_repair()
        self.assign('repair', 'repair-reviewer', context_repair={
            'assignment_id': 'batch', 'event_ids': ['exposure'], 'probe_id': 'repair-probe',
            'reason': 'Retain the actual scientific source batch after context contamination.',
            'evidence': [self.evidence]})
        repaired = self.packet('repair')
        self.assertEqual(repaired['pending_source_requests'], sent['pending_source_requests'])
        historical = repaired['scientific_history'][-1]['observed_output']
        self.assertEqual(historical['value']['status'], output['value']['status'])
        self.assertEqual(historical['limitations'], output['limitations'])
        withheld = {entry['artifact']['sha256'] for entry in sent['evidence']
                    if entry.get('delivery') == 'body-deferred-source-capacity'}
        self.assertFalse(withheld & {entry['sha256'] for entry in repaired['previously_supplied_evidence']})

    def test_all_batches_settle_only_after_every_complete_source_is_actually_delivered(self):
        self.observed_batch()
        parent, seen = 'batch', set()
        for index in range(3):
            records = self.store.snapshot()['records']
            state = review_protocol.assignment_state(records, self.artifacts, parent)
            request = json.loads(self.artifacts.read(records['review_attempt'][state['attempt_id']]['request']))
            actual = json.loads(request['input'][-1]['content'].split('\n', 1)[1])
            for delivery in actual['source_deliveries']:
                if delivery['status'] == 'delivered':
                    seen.add(delivery['request']['version_id'])
            if not state['pending_source_requests']:
                self.assertEqual(seen, set(self.versions))
                self.record_final(state['output'], parent)
                break
            child = 'followup-%d' % index
            with review_transport.text_only_delivery(True):
                self.assign(child, prior_assignment_id=parent)
            self.invoke(self.review_response('bar'), child)
            parent = child
        else:
            self.fail('Whole-source batches did not finish within the finite source count.')
        self.assertIn('final', self.store.snapshot()['records']['value_review'])
