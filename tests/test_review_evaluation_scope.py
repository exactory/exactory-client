"""Native visual validation shares work only within one immutable records view."""

import copy
import json
import unittest
from unittest.mock import patch

from research_harness import review_pdf_pages as pages, review_visual_route as bridge
from research_harness import review_visual_context_repair as repair
from research_harness.errors import ResearchError
from research_harness.evaluation import Evaluation
import test_visual_bridge_origin as bridge_fixtures
import test_visual_context_repair as repair_fixtures


class BridgeEvaluationScopeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = bridge_fixtures.VisualBridgeOriginTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.records = self.fixture.case.read_records()
        self.artifacts = self.fixture.artifacts
        self.child, self.parent = self.fixture.child, self.fixture.parent

    def validate(self, records, artifacts, child=None):
        return bridge.validate_lineage_bridge(records, artifacts, child or self.child, self.parent)

    def test_same_view_renders_once_but_runs_the_complete_transition_check_each_time(self):
        evaluation = Evaluation(self.records, self.artifacts)
        with patch.object(pages, 'render_bytes', wraps=pages.render_bytes) as render, \
                patch.object(bridge, 'validate_transition', wraps=bridge.validate_transition) as validate:
            first = self.validate(self.records, evaluation)
            second = self.validate(self.records, evaluation)
        self.assertEqual(first, second)
        self.assertEqual(validate.call_count, 2)
        self.assertEqual(render.call_count, 1)

    def test_new_snapshot_and_other_origin_cutoff_repeat_native_page_validation(self):
        evaluation = Evaluation(self.records, self.artifacts)
        with patch.object(pages, 'render_bytes', wraps=pages.render_bytes) as render:
            self.validate(self.records, evaluation)
            self.validate(self.records, evaluation)
            later_child = dict(self.child, recorded_revision=self.child['recorded_revision'] + 1)
            self.validate(self.records, evaluation, later_child)
            self.validate(self.records, evaluation, later_child)
            fresh = copy.deepcopy(self.records)
            self.validate(fresh, evaluation)
        self.assertEqual(render.call_count, 3)

    def test_changed_manifest_and_new_snapshot_probe_failure_still_reject(self):
        evaluation = Evaluation(self.records, self.artifacts)
        self.validate(self.records, evaluation)
        packet = json.loads(self.artifacts.read(self.child['packet']))
        packet['visual_source_delivery']['pages'][0]['width'] += 1
        changed_child = dict(self.child, packet=self.artifacts.put(json.dumps(packet).encode(), 'application/json'))
        with self.assertRaises(ResearchError) as malformed:
            self.validate(self.records, evaluation, changed_child)
        self.assertEqual(malformed.exception.code, 'review_visual_source_mismatch')
        fresh = copy.deepcopy(self.records)
        fresh['review_route_probe']['visual-probe']['status'] = 'failed'
        with self.assertRaises(ResearchError) as failed_probe:
            self.validate(fresh, evaluation)
        self.assertEqual(failed_probe.exception.code, 'review_visual_route_mismatch')


class RepairEvaluationScopeTests(unittest.TestCase):
    def test_repeated_repair_uses_one_frozen_view_and_keeps_all_history_checks(self):
        fixture = repair_fixtures.VisualContextRepairTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        assignment = fixture.assign()
        records = fixture.case.read_records()
        packet = json.loads(fixture.artifacts.read(assignment['packet']))
        evaluation = Evaluation(records, fixture.artifacts)
        with patch.object(repair, '_science', wraps=repair._science) as science:
            first = repair.validate(records, evaluation, assignment, packet)
            second = repair.validate(records, evaluation, assignment, packet)
        self.assertEqual(first, second)
        self.assertEqual(science.call_count, 2)
        first_records, first_artifacts = science.call_args_list[0].args[:2]
        second_records, second_artifacts = science.call_args_list[1].args[:2]
        self.assertIs(first_records, second_records)
        self.assertIs(first_artifacts, second_artifacts)
        self.assertIs(first_artifacts.records, first_records)
        changed = copy.deepcopy(packet)
        changed['scientific_history'][-1]['observed_output']['value']['reason'] = 'A substituted scientific conclusion.'
        with self.assertRaises(ResearchError) as invalid:
            repair.validate(records, evaluation, assignment, changed)
        self.assertEqual(invalid.exception.code, 'review_visual_repair_mismatch')


if __name__ == '__main__':
    unittest.main()
