"""A later route probe cannot rewrite the origin of a recorded visual bridge."""

import copy
import json
import unittest
from unittest.mock import patch

from research_harness import review_protocol as protocol, review_transport as transport
from research_harness import review_visual_route as bridge
from research_harness.errors import ResearchError


class VisualBridgeOriginTests(unittest.TestCase):
    def setUp(self):
        from test_visual_result_delivery import VisualResultDeliveryTests
        self.fixture = VisualResultDeliveryTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.case, self.artifacts = self.fixture.case, self.fixture.artifacts
        self.child = self.fixture.assignment
        self.parent = self.case.read_records()['review_assignment'][self.child['history_assignment_id']]

    def validate(self, records, child=None):
        bridge.validate_lineage_bridge(records, self.artifacts, child or self.child, self.parent)

    def probe(self, identifier, *, passed):
        def respond(route, request, credential):
            positive = self.case.read_records()['review_probe_invocation'][identifier]['positive_controls']
            output = {key: positive[key] for key in ('allowed_control', 'evidence_control')}
            if not passed:
                output['allowed_control'] = 'incorrect'
            output['excluded_controls'] = []
            return self.fixture.response(route, request, output)
        with patch.object(transport, 'send_request', side_effect=respond):
            self.case.mutate(protocol.probe_route, {'id': identifier, 'route_id': self.child['route_id']},
                             credential='test-only')

    def test_later_success_and_failure_preserve_origin_but_current_failure_still_disqualifies(self):
        self.fixture.observe([])
        before = self.case.read_records()
        attempt = before['review_attempt']['actual-visual-attempt']
        self.assertTrue(protocol.authenticate_provenance(before, self.artifacts, self.child, attempt))
        self.probe('later-success', passed=True)
        records = self.case.read_records()
        self.validate(records)
        self.assertTrue(protocol.authenticate_provenance(records, self.artifacts, self.child, attempt))
        self.probe('later-failure', passed=False)
        records = self.case.read_records()
        self.validate(records)
        self.assertTrue(protocol.authenticate_provenance(records, self.artifacts, self.child, attempt))
        self.assertEqual(protocol.route_state(records, self.artifacts, self.child['route_id'])['context_status'],
                         'unverified')
        self.assertFalse(protocol.assignment_state(records, self.artifacts, self.child['id'])['ready'])

    def test_missing_altered_future_and_failed_original_probe_are_rejected(self):
        records = self.case.read_records()
        original = records['review_route_probe']['visual-probe']
        for defect in ('missing', 'altered-output', 'future', 'failed-before-child', 'newer-failed-before-child'):
            changed = copy.deepcopy(records)
            saved = changed['review_route_probe']['visual-probe']
            child = self.child
            if defect == 'missing':
                del changed['review_route_probe']['visual-probe']
            elif defect == 'altered-output':
                output = json.loads(self.artifacts.read(saved['output']))
                output['allowed_control'] = 'incorrect'
                saved['output'] = self.artifacts.put(json.dumps(output).encode(), 'application/json')
            elif defect == 'future':
                saved['recorded_revision'] = self.child['recorded_revision'] + 1
            elif defect == 'failed-before-child':
                saved['status'] = 'failed'
            else:
                child = dict(self.child, recorded_revision=self.child['recorded_revision'] + 1)
                changed['review_route_probe']['intervening-failure'] = dict(saved, id='intervening-failure',
                    status='failed', recorded_revision=self.child['recorded_revision'])
            with self.subTest(defect=defect), self.assertRaises(ResearchError):
                self.validate(changed, child)
        self.assertLess(original['recorded_revision'], self.child['recorded_revision'])


if __name__ == '__main__':
    unittest.main()
