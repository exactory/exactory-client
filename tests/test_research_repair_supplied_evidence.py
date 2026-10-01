"""A fresh repair retains actual prior attachments without widening excerpts."""

import base64
import json

from strategy_fixtures import StrategyCase
import test_research_bar_sources as source_fixtures
import test_research_repair_source_pending as repair_fixtures
from research_harness import reading, review_protocol


class RepairSuppliedEvidenceTests(StrategyCase):
    metadata = source_fixtures.BarSourceTests.metadata
    capture = source_fixtures.BarSourceTests.capture
    span = source_fixtures.BarSourceTests.span
    link = source_fixtures.BarSourceTests.link
    abstract_note = source_fixtures.BarSourceTests.abstract_note
    visual_source = source_fixtures.BarSourceTests.visual_source
    assign = source_fixtures.BarSourceTests.assign
    packet = source_fixtures.BarSourceTests.packet
    invoke = source_fixtures.BarSourceTests.invoke
    request = source_fixtures.BarSourceTests.request
    expose = repair_fixtures.RepairSourcePendingTests.expose
    probe_repair = repair_fixtures.RepairSourcePendingTests.probe_repair
    repair = repair_fixtures.RepairSourcePendingTests.repair

    def setUp(self):
        super().setUp()
        self.sequence = 0
        self.setup_dossier()
        self.route()

    def advance_to_inventory_only(self):
        self.request([{'id': 'first-page', 'kind': 'inventory_page', 'page': 0}], 'b')
        self.assign('c', prior_assignment_id='b')
        self.request([{'id': 'last-page', 'kind': 'inventory_page', 'page': 0}], 'c')
        self.repair('c')

    def test_binary_delivered_in_earlier_turn_is_reattached_in_fresh_repair_request(self):
        source, visual = self.visual_source()
        self.assign()
        self.request([{'id': 'body', 'kind': 'source', 'version_id': source, 'depth': 'fulltext'}])
        self.assign('b', prior_assignment_id='a')
        image = next(entry['artifact'] for entry in self.packet('b')['evidence']
                     if entry['artifact']['media_type'] == 'image/png')
        self.advance_to_inventory_only()
        self.request([{'id': 'next-page', 'kind': 'inventory_page', 'page': 0}], 'repair')
        records = self.store.snapshot()['records']
        observed = json.loads(self.artifacts.read(records['review_attempt']['attempt-repair']['request']))
        delivered = [part['image_url'] for message in observed['input'] if isinstance(message['content'], list)
                     for part in message['content'] if part['type'] == 'input_image']
        self.assertEqual(delivered, ['data:image/png;base64,' + base64.b64encode(self.artifacts.read(image)).decode()])
        self.assertEqual(records['review_assignment']['repair']['history'], [])
        self.assertIn(image, [entry['artifact'] for entry in self.packet('repair')['evidence']])

    def test_exact_prior_excerpt_is_redelivered_without_unsupplied_original_tail(self):
        source = self.metadata(1, abstract='ONLY ACTUAL SUPPLIED PASSAGE. UNSUPPLIED ORIGINAL TAIL.')
        self.mutate(reading.record_reading, self.abstract_note(source))
        abstract = self.store.snapshot()['records']['work'][source]['abstracts'][0]
        link = self.link(source, abstract['source_id'], abstract['artifact'], 'ONLY ACTUAL SUPPLIED PASSAGE.')
        self.assign(source_links=[link])
        excerpt = self.packet()['prior_context'][0]['excerpt']
        self.request([{'id': 'initial-page', 'kind': 'inventory_page', 'page': 0}])
        self.assign('b', prior_assignment_id='a')
        self.advance_to_inventory_only()
        references = [entry['artifact'] for entry in self.packet('repair')['evidence']]
        self.assertIn(excerpt, references)
        self.assertNotIn(abstract['artifact'], references)
        self.assertNotIn('UNSUPPLIED ORIGINAL TAIL.', json.dumps(self.packet('repair')))
