"""The public assignment operation stores only a validated same-slot visual bridge."""

import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from research_harness import review_protocol as protocol, review_transport as transport
from research_harness import review_visual_route as bridge
from research_harness.errors import ResearchError
from research_harness.evidence import digest


class VisualAssignmentIntegrationTests(unittest.TestCase):
    def setUp(self):
        import test_research_visual_route as fixtures
        self.case = fixtures.VisualRouteTests()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.records = self.case.records
        self.records['workspace'] = {'problem': {'orchestration': 'problem_creation', 'authors': ['Author']}}
        self.case.prior['session_id'] = 'original-session'
        self.value = {'id': 'visual-result', 'route_id': 'visual', 'dossier_id': None, 'role': 'result',
                      'reviewer_id': 'Astra', 'author_id': 'Author', 'context': {'prior_assignment_id': 'prior'}}
        self.packet = copy.deepcopy(self.case.packet)
        self.packet['evidence'] = []
        self.packet['digest'] = digest(self.packet)
        self.store = SimpleNamespace(root=self.case.fixture.root, build_artifact_store=lambda: self.case.artifacts)
        def prepare_only(store, operation, value, prepare, **options):
            return prepare(self.records, value)[1]
        for name, options in [
            ('prepared_mutation', {'side_effect': prepare_only}),
            ('build_problem_subject', {'return_value': self.packet['subject']}),
            ('_source_parent', {'return_value': (self.case.prior, {'ready': True})}),
            ('build_packet', {'side_effect': lambda *args, **kwargs: copy.deepcopy(self.packet)}),
        ]:
            active = patch.object(protocol, name, **options)
            active.start()
            self.addCleanup(active.stop)

    def assign(self):
        return protocol.record_assignment(self.store, self.value, expected_revision=1, request_id='visual-assignment')

    def test_public_assignment_stores_native_pages_exact_history_and_validated_bridge(self):
        assignment = self.assign()
        packet = json.loads(self.case.artifacts.read(assignment['packet']))
        self.assertEqual(assignment['slot_id'], 'slot')
        self.assertEqual(assignment['session_id'], 'original-session')
        self.assertEqual(assignment['history_assignment_id'], 'prior')
        self.assertEqual(assignment['history'], self.case.context['history'])
        self.assertEqual(packet[bridge.MARKER]['previous_assignment_id'], 'prior')
        self.assertTrue(packet['visual_source_delivery']['pages'])
        self.assertEqual(assignment['packet_digest'], packet['digest'])
        before_digest = dict(packet)
        before_digest.pop('digest')
        self.assertEqual(packet['digest'], digest(before_digest))

    def test_public_assignment_cannot_bypass_final_bridge_validation(self):
        with patch.object(bridge, 'validate_transition', side_effect=ResearchError('review_visual_route_mismatch', 'Rejected bridge')):
            with self.assertRaises(ResearchError) as failure:
                self.assign()
        self.assertEqual(failure.exception.code, 'review_visual_route_mismatch')

    def test_base_packet_preserves_original_pdf_omissions_before_native_pages_are_attached(self):
        observed = []
        def build(*args, **kwargs):
            observed.append(transport.text_only_enabled())
            return copy.deepcopy(self.packet)
        with patch.object(protocol, 'build_packet', side_effect=build):
            assignment = self.assign()
        self.assertEqual(observed, [True])
        packet = json.loads(self.case.artifacts.read(assignment['packet']))
        self.assertTrue(packet['visual_source_delivery']['pages'])

    def test_crossing_requires_new_current_reading_pages_and_preserves_model(self):
        self.packet['problem_evidence']['readings'] = []
        with self.assertRaises(ResearchError) as failure:
            self.assign()
        self.assertEqual(failure.exception.code, 'review_visual_route_mismatch')
        self.packet = copy.deepcopy(self.case.packet)
        self.records['review_route']['visual']['model'] = 'gpt-6-sol'
        with self.assertRaises(ResearchError) as failure:
            self.assign()
        self.assertEqual(failure.exception.code, 'review_visual_route_mismatch')

    def test_unrelated_route_change_keeps_the_original_refusal(self):
        self.records['review_route']['visual']['adapter'] = 'claude_cli_v1'
        with self.assertRaises(ResearchError) as failure:
            self.assign()
        self.assertEqual(failure.exception.code, 'review_source_continuation_mismatch')


if __name__ == '__main__':
    unittest.main()
