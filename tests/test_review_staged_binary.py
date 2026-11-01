"""Staged binary credit comes from actual native page inputs, never descriptors."""

import copy
import sys
import unittest
from unittest.mock import patch

from problem_parts_fixtures import ResultPartsCase
from research_harness import cli
from research_harness import review_staged as staged
from research_harness import review_staged_protocol as protocol
from research_harness import review_protocol as ordinary
from research_harness import review_visual_inputs, review_visual_sources
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from test_review_staged_fixtures import epoch, task


class StagedBinaryTests(unittest.TestCase):
    def setUp(self):
        from test_research_visual_sources import VisualSourceTests
        fixture = VisualSourceTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.native = fixture
        self.records, self.artifacts = fixture.records, fixture.artifacts
        self.original = fixture.capture_value['original']
        source = protocol.make_artifact_source(self.artifacts, self.original)
        manifest = staged.build_manifest({'statement': 'The native page supports the claim.'},
            [dict(key='readings', index=0, item=dict(id=fixture.native_reading['id']),
                  artifacts=[self.original], source_ids=[])], [source])
        self.epoch = epoch(manifest=manifest)
        self.binary = dict(source_id=source['id'], descriptor=self.original)
        self.task = task(self.epoch, binary_sources=[self.binary])
        self.route = dict(adapter=review_visual_inputs.ADAPTER, model='fixture-model',
                          configuration=dict(max_output_tokens=1000))
        self.source_packet = copy.deepcopy(fixture.packet)

    def packet(self, assigned=None):
        assigned = assigned or self.task
        return dict(protocol=staged.PROTOCOL, subject=self.epoch['subject'], role='result',
                    staged_review=dict(task=dict(id=assigned['id'])), evidence=[])

    def prepared(self, assigned=None):
        from research_harness import review_staged_binary as binary
        return binary.prepare_packet(self.records, self.artifacts, self.route, self.epoch,
            assigned or self.task, self.packet(assigned), source_packet=self.source_packet)

    def request(self, packet):
        return review_visual_inputs.source_request(self.route, 'Inspect the supplied original.', packet)

    def test_native_pages_supply_the_exact_assigned_binary_and_actual_images(self):
        from research_harness import review_staged_binary as binary
        packet = self.prepared()
        request = self.request(packet)
        self.assertEqual(len(request['exactory_image_inputs']), 1)
        self.assertEqual(packet['staged_review']['binary_sources'], [self.binary])
        self.assertEqual(binary.read_actual_sources(self.records, self.artifacts, self.epoch,
                                               self.task, request), [self.binary])
        self.assertTrue(binary.preserves_packet(self.records, self.artifacts, packet, request))
        self.assertEqual(self.source_packet, self.native.packet)

    def test_text_route_is_structured_unsupported_without_permanent_pending(self):
        from research_harness import review_staged_binary as binary
        with self.assertRaises(ResearchError) as failure:
            binary.prepare_packet(self.records, self.artifacts, dict(self.route, adapter='codex_cli_v1'),
                self.epoch, self.task, self.packet(), source_packet=self.source_packet)
        self.assertEqual(failure.exception.code, 'review_staged_binary_unsupported')
        self.assertEqual(failure.exception.details['source_ids'], [self.binary['source_id']])
        self.assertEqual(failure.exception.details['reason'], 'route_lacks_native_binary_delivery')

    def test_binary_without_native_reading_delivery_is_explicitly_raise_unsupported(self):
        from research_harness import review_staged_binary as binary
        with self.assertRaises(ResearchError) as failure:
            binary.prepare_packet(self.records, self.artifacts, self.route, self.epoch,
                                  self.task, self.packet())
        self.assertEqual(failure.exception.code, 'review_staged_binary_unsupported')
        self.assertEqual(failure.exception.details['reason'], 'native_original_unavailable')

    def test_arbitrary_binary_with_no_qualified_native_contract_is_raise_unsupported(self):
        from research_harness import review_staged_binary as binary
        for data, media in ((b'\xff\x00\xff', 'application/octet-stream'),
                            (review_visual_inputs.control_image('Original PNG'), 'image/png')):
            with self.subTest(media=media):
                reference = self.artifacts.put(data, media)
                source = protocol.make_artifact_source(self.artifacts, reference)
                manifest = staged.build_manifest({'statement': 'The binary supports the claim.'},
                    [dict(key='outputs', index=0, item=dict(artifact=reference),
                          artifacts=[reference], source_ids=[])], [source])
                value = epoch(manifest=manifest)
                assigned = task(value, binary_sources=[dict(source_id=source['id'], descriptor=reference)])
                with self.assertRaises(ResearchError) as failure:
                    binary.prepare_packet(self.records, self.artifacts, self.route, value, assigned,
                                          self.packet(assigned), source_packet=self.source_packet)
                self.assertEqual(failure.exception.code, 'review_staged_binary_unsupported')
                self.assertEqual(failure.exception.details['reason'], 'native_original_unavailable')

    def test_missing_actual_image_cannot_receive_binary_credit(self):
        from research_harness import review_staged_binary as binary
        packet = self.prepared()
        request = self.request(packet)
        request['exactory_image_inputs'] = []
        self.assertFalse(binary.preserves_packet(self.records, self.artifacts, packet, request))
        with self.assertRaises(ResearchError):
            binary.read_actual_sources(self.records, self.artifacts, self.epoch, self.task, request)

    def test_unrelated_real_image_cannot_replace_the_native_original(self):
        from research_harness import review_staged_binary as binary
        packet = self.prepared()
        request = self.request(packet)
        unrelated = review_visual_inputs.control_image('An unrelated page')
        reference = self.artifacts.put(unrelated, 'image/png')
        import base64
        request['exactory_image_inputs'] = [dict(artifact=reference,
            data_url='data:image/png;base64,' + base64.b64encode(unrelated).decode('ascii'))]
        with self.assertRaises(ResearchError):
            binary.read_actual_sources(self.records, self.artifacts, self.epoch, self.task, request)

    def test_forged_native_reading_digest_cannot_receive_binary_credit(self):
        from research_harness import review_staged_binary as binary
        packet = self.prepared()
        packet[review_visual_sources.MARKER]['readings'][0]['digest'] = '0' * 64
        request = self.request(packet)
        with self.assertRaises(ResearchError):
            binary.read_actual_sources(self.records, self.artifacts, self.epoch, self.task, request)

    def test_valid_native_page_still_needs_complete_original_coverage(self):
        from research_harness import review_staged_binary as binary
        from unittest.mock import patch
        with patch('research_harness.source_links._read_output_pdf_page_count', return_value=2):
            with self.assertRaises(ResearchError) as failure:
                self.prepared()
        self.assertEqual(failure.exception.code, 'review_staged_binary_unsupported')
        self.assertEqual(failure.exception.details['reason'], 'native_original_pages_incomplete')

    def test_visual_text_task_has_fresh_empty_native_contract_and_no_binary_credit(self):
        from research_harness import review_staged_binary as binary
        assigned = task(self.epoch, binary_sources=[])
        packet = self.prepared(assigned)
        self.assertEqual(packet[review_visual_sources.MARKER]['pages'], [])
        self.assertEqual(binary.read_actual_sources(self.records, self.artifacts, self.epoch,
                                               assigned, self.request(packet)), [])
        self.assertTrue(binary.preserves_packet(self.records, self.artifacts, packet, self.request(packet)))

    def test_native_image_delivery_does_not_allow_staged_scientific_text_changes(self):
        from research_harness import review_staged_binary as binary
        packet = self.prepared()
        altered = copy.deepcopy(packet)
        altered['staged_review']['task']['id'] = 'a-different-task'
        self.assertFalse(binary.preserves_packet(self.records, self.artifacts, packet, self.request(altered)))

    def test_text_route_without_binary_scope_retains_ordinary_packet(self):
        from research_harness import review_staged_binary as binary
        assigned = task(self.epoch, binary_sources=[])
        packet = self.packet(assigned)
        self.assertIs(binary.prepare_packet(self.records, self.artifacts,
            dict(self.route, adapter='codex_cli_v1'), self.epoch, assigned, packet), packet)
        self.assertNotIn(review_visual_sources.MARKER, packet)

    def test_task_cannot_substitute_a_different_binary_descriptor(self):
        from research_harness import review_staged_binary as binary
        assigned = copy.deepcopy(self.task)
        assigned['binary_sources'][0]['descriptor']['sha256'] = '0' * 64
        with self.assertRaises(ResearchError) as failure:
            self.prepared(assigned)
        self.assertEqual(failure.exception.code, 'review_task_dependency_changed')

    def test_metadata_only_claim_cannot_replace_a_native_binary_delivery(self):
        from research_harness import review_staged_binary as binary
        packet = self.prepared()
        packet[review_visual_sources.MARKER].update(sources=[], pages=[], readings=[])
        request = self.request(packet)
        with self.assertRaises(ResearchError):
            binary.read_actual_sources(self.records, self.artifacts, self.epoch, self.task, request)

    def test_prepare_task_plan_with_artifact_store_freezes_exact_native_request(self):
        from research_harness import review_staged_binary as binary
        from research_harness import review_excerpts
        route = dict(self.route, id=self.epoch['route_id'], endpoint='codex://local',
                     configuration=dict(max_output_tokens=1000, timeout_seconds=600))
        records = dict(self.records, review_route={route['id']:route})
        controller = dict(id=self.epoch['controller_assignment_id'], route_id=route['id'],
                          packet=ordinary._put_json(self.artifacts, self.source_packet))
        planned = protocol.prepare_task_plan(records, self.artifacts, controller, self.epoch, self.task)
        request = ordinary._read_json(self.artifacts, planned['request'])
        sent = review_excerpts.read_sent_packet(request)
        self.assertEqual(request['history'], [])
        self.assertEqual(sent['staged_review']['binary_sources'], [self.binary])
        self.assertEqual(planned['task']['planned_request_digest'], digest(request))
        self.assertEqual(binary.read_actual_sources(records, self.artifacts, self.epoch,
                                               planned['task'], request), [self.binary])


class StagedBinaryOutcomeTests(ResultPartsCase):
    staged_entry = True

    def test_text_binary_frontier_records_execution_incomplete_and_resumes_stably(self):
        from research_harness import review_worker
        from test_review_staged_frontier import WORKER
        reference = self.case.artifacts.put(b'\xff\x00\xff', 'application/octet-stream')
        manifest = staged.build_manifest({'statement':'The opaque original supports the claim.'},
            [dict(key='assessment_evidence', index=0, item=dict(artifact=reference),
                  artifacts=[reference], source_ids=[])],
            [protocol.make_artifact_source(self.case.artifacts, reference)])
        prior = self.assign('binary-prior')
        with patch.object(protocol, 'build_manifest', return_value=manifest):
            controller = self.case.assign('result', 'first', identifier='binary-controller',
                                          staged_upgrade=dict(assignment_id=prior['id']))

        def command(name, payload):
            return cli.OPERATIONS[name](self.case.store, payload,
                expected_revision=self.case.store.revision,
                request_id='binary-command-' + str(self.case.store.revision))['result']

        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', WORKER]):
            for _ in range(10):
                planned = command('review-assignment', dict(controller_assignment_id=controller['id']))
                if planned.get('outcome'):
                    break
                command('review-run', planned['next'][0]['payload'])
            else:
                self.fail('The unsupported native original remained pending')
        outcome = planned['outcome']
        records = self.case.read_records()
        self.assertEqual((outcome['kind'], outcome['origin']), ('assessment_incomplete', 'execution'))
        self.assertIn(outcome['id'], records['assessment_incomplete'])
        self.assertEqual(records['review_slot_head'][controller['slot_id']]['state'], 'incomplete')
        self.assertTrue(outcome['origin_binding']['execution_receipts'])
        self.assertTrue(any(row['scope']['binary_sources'] for row in outcome['unresolved_scope']))
        self.assertNotIn('read', (self.case.root / 'calls').read_text().splitlines())
        resumed = command('review-assignment', dict(controller_assignment_id=controller['id']))
        self.assertEqual(resumed['outcome'], outcome)
        self.assertFalse(any(row.get('command') == 'review-run' for row in resumed['next']))


if __name__ == '__main__':
    unittest.main()
