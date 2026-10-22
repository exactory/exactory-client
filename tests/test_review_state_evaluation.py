"""State reuse preserves current runtime, immutable views and complete refusals."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.evaluation import Evaluation
from research_harness import review_state_evaluation as states, review_transport as transport


class StateEvaluationBoundaryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.artifacts = ArtifactStore(Path(directory.name))
        self.records = {'review_route': {name: {'id': name, 'adapter': name}
                        for name in ('visual', 'historical-text', 'correction')}}
        self.evaluation = Evaluation(self.records, self.artifacts)
        self.versions = {name: 1 for name in self.records['review_route']}
        observation = patch.object(transport, 'runtime', side_effect=lambda route:
                                   {'adapter': route['adapter'], 'version': self.versions[route['adapter']]})
        self.runtime = observation.start()
        self.addCleanup(observation.stop)
        self.result = {'ready': True, 'output': {'limitations': ['Retain the actual scientific limitation.']},
                       'obligations': [], 'refused_attempts': [{'attempt_id': 'first', 'reason': 'Exact original refusal.'}]}
        self.compute = Mock(side_effect=lambda records, artifacts, identifier: copy.deepcopy(self.result))

    def evaluate(self, records=None, compute=None, identifier='leaf'):
        return states.evaluate_state(self.records if records is None else records, self.evaluation,
                                     'assignment', identifier, self.compute if compute is None else compute)

    def test_reuse_still_observes_every_adapter_on_each_public_call(self):
        self.assertEqual(self.evaluate(), self.result)
        before = self.runtime.call_count
        self.assertEqual(self.evaluate(), self.result)
        self.assertEqual(self.compute.call_count, 1)
        self.assertEqual(self.runtime.call_count - before, 3)

    def test_changed_historical_text_or_separate_correction_runtime_invalidates_leaf(self):
        for adapter in ('historical-text', 'correction'):
            with self.subTest(adapter=adapter):
                self.result['ready'] = True
                self.assertTrue(self.evaluate(identifier=adapter)['ready'])
                count = self.compute.call_count
                self.versions[adapter] += 1
                self.result['ready'] = False
                self.result['obligations'] = [{'code': 'review_invocation_unverified'}]
                result = self.evaluate(identifier=adapter)
                self.assertFalse(result['ready'])
                self.assertEqual(result['obligations'], self.result['obligations'])
                self.assertEqual(self.compute.call_count, count + 1)

    def test_returned_nested_output_obligations_and_refusals_cannot_poison_cached_state(self):
        self.result.update(ready=False, obligations=[{'code': 'original', 'details': ['preserved']}])
        first = self.evaluate()
        first['output']['limitations'].clear()
        first['obligations'][0]['details'][0] = 'substituted'
        first['refused_attempts'][0]['reason'] = 'substituted'
        self.assertEqual(self.evaluate(), self.result)
        self.assertEqual(self.compute.call_count, 1)

    def test_negative_state_does_not_cross_snapshot_or_historical_cutoff(self):
        self.result['ready'] = False
        self.assertFalse(self.evaluate()['ready'])
        self.result['ready'] = True
        fresh = copy.deepcopy(self.records)
        self.assertTrue(self.evaluate(fresh)['ready'])
        historical = dict(self.records, review_attempt={})
        self.assertTrue(self.evaluate(historical)['ready'])
        self.assertEqual(self.compute.call_count, 3)
        self.assertFalse(self.evaluate()['ready'])

    def test_unrelated_observation_failure_uses_native_computation_without_new_refusal(self):
        def unavailable(route):
            if route['adapter'] == 'correction':
                raise ResearchError('review_runtime_missing', 'An unrelated adapter is unavailable.')
            return {'adapter': route['adapter'], 'version': 1}
        self.runtime.side_effect = unavailable
        self.assertEqual(self.evaluate(), self.result)
        self.assertEqual(self.evaluate(), self.result)
        self.assertEqual(self.compute.call_count, 2)

    def test_runtime_observation_failure_cannot_reuse_a_warm_positive_state(self):
        self.assertTrue(self.evaluate()['ready'])
        self.runtime.side_effect = ResearchError('review_runtime_missing', 'The native executable is unavailable.')
        self.compute.side_effect = ResearchError('review_runtime_missing', 'The native executable is unavailable.')
        for _ in range(2):
            with self.assertRaises(ResearchError) as error:
                self.evaluate()
            self.assertEqual(error.exception.code, 'review_runtime_missing')
        self.assertEqual(self.compute.call_count, 3)

    def test_native_exception_is_not_cached_and_active_guard_is_released(self):
        self.compute.side_effect = [ResearchError('artifact_corrupt', 'The original bytes do not match.'), self.result]
        with self.assertRaises(ResearchError):
            self.evaluate()
        self.assertEqual(self.evaluate(), self.result)
        self.assertEqual(self.evaluate(), self.result)
        self.assertEqual(self.compute.call_count, 2)

    def test_fingerprint_change_during_computation_is_not_saved(self):
        def changing(records, artifacts, identifier):
            self.versions['historical-text'] += 1
            return copy.deepcopy(self.result)
        self.compute.side_effect = changing
        self.evaluate()
        self.compute.side_effect = lambda records, artifacts, identifier: copy.deepcopy(self.result)
        self.evaluate()
        self.evaluate()
        self.assertEqual(self.compute.call_count, 2)

    def test_recursive_cycle_rejects_even_if_runtime_changes_and_does_not_poison_later_call(self):
        def recursive(records, artifacts, identifier):
            self.versions['visual'] += 1
            return states.evaluate_state(records, artifacts, 'assignment', identifier, recursive)
        with self.assertRaises(ResearchError) as error:
            self.evaluate(compute=recursive)
        self.assertEqual(error.exception.code, 'review_context_repair_mismatch')
        self.assertEqual(self.evaluate(), self.result)


class NativePublicStateTests(unittest.TestCase):
    def setUp(self):
        import test_visual_bridge_origin as fixtures
        self.fixture = fixtures.VisualBridgeOriginTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.fixture.fixture.observe([])
        self.records = self.fixture.case.read_records()
        self.evaluation = Evaluation(self.records, self.fixture.artifacts)

    def test_public_assignment_reuses_complete_native_state_and_rejects_new_failed_probe(self):
        from research_harness import review_protocol as protocol
        identifier = self.fixture.child['id']
        with patch.object(protocol, '_assignment_state', wraps=protocol._assignment_state) as compute:
            first = protocol.assignment_state(self.records, self.evaluation, identifier)
            second = protocol.assignment_state(self.records, self.evaluation, identifier)
        self.assertTrue(first['ready'])
        self.assertEqual(first, second)
        self.assertEqual(sum(call.args[2] == identifier for call in compute.call_args_list), 1)
        fresh = copy.deepcopy(self.records)
        fresh['review_route_probe']['visual-probe']['status'] = 'failed'
        rejected = protocol.assignment_state(fresh, self.evaluation, identifier)
        self.assertFalse(rejected['ready'])
        self.assertIn('review_context_unverified', [row['code'] for row in rejected['obligations']])

    def test_public_route_reuses_native_probe_validation_but_does_not_return_mutable_cache(self):
        from research_harness import review_protocol as protocol
        identifier = self.fixture.child['route_id']
        with patch.object(protocol, '_route_state', wraps=protocol._route_state) as compute:
            first = protocol.route_state(self.records, self.evaluation, identifier)
            expected = copy.deepcopy(first)
            first['residual_exposures'].clear()
            self.assertEqual(protocol.route_state(self.records, self.evaluation, identifier), expected)
        self.assertEqual(expected['context_status'], 'verified_for_route')
        self.assertEqual(compute.call_count, 1)


class NativeEmitterProfileBoundaryTests(unittest.TestCase):
    def test_changed_emitter_bytes_invalidate_a_warm_native_visual_repair(self):
        import json
        import test_visual_context_repair as fixtures
        from research_harness import review_exact_text_cost_v1 as emitter, review_protocol as protocol
        fixture = fixtures.VisualContextRepairTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        repaired = fixture.assign()
        packet = json.loads(fixture.artifacts.read(repaired['packet']))
        response = copy.deepcopy(fixture.output)
        response['reassessment'] = {'assignment_id': fixture.prior['id'], 'findings': [
            {'review_id': row['id'], 'disposition': 'revised',
             'reason': 'The exact fresh supplied source evidence supports this assessment.',
             'evidence': [{'kind': 'source', 'link': fixture.fixture.pdf_link}]}
            for row in packet['historical_reviews']]}
        with patch.object(transport, 'send_request', side_effect=lambda route, request, credential:
                          fixture.fixture.response(route, request, response)):
            fixture.case.mutate(protocol.invoke_assignment, {'id': 'profile-repair-attempt',
                'assignment_id': repaired['id']}, credential='test-only')
        records = fixture.case.read_records()
        evaluation = Evaluation(records, fixture.artifacts)
        original = Path(emitter.__file__).read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'review_exact_text_cost_v1.py'
            source.write_bytes(original)
            with patch.object(emitter, '__file__', str(source)):
                first = protocol.assignment_state(records, evaluation, repaired['id'])
                self.assertTrue(first['ready'])
                self.assertEqual(protocol.assignment_state(records, evaluation, repaired['id']), first)
                source.write_bytes(original + b'\n# A changed native emitter implementation.\n')
                rejected = protocol.assignment_state(records, evaluation, repaired['id'])
                self.assertFalse(rejected['ready'])
                self.assertIn('review_invocation_unverified', [row['code'] for row in rejected['obligations']])
                source.write_bytes(original)
                self.assertEqual(protocol.assignment_state(records, evaluation, repaired['id']), first)


if __name__ == '__main__':
    unittest.main()
