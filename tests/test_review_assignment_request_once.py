"""Text requests are memoized per records view; visual requests observe the current emitter."""

import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

from problem_parts_fixtures import KEY, ResultPartsCase
from research_harness import review_protocol as protocol, review_transport as transport
from research_harness.errors import ResearchError
from research_harness.evaluation import Evaluation
from research_harness.storage import Store


class AssignmentPreparationEvaluationTests(ResultPartsCase):
    def test_part_turn_preparation_uses_one_root_evaluation_and_preserves_assignment(self):
        self.oversized()
        first = self.assign()
        self.invoke(first)
        payload = {'id': 'part-2', 'route_id': 'route', 'dossier_id': None, 'role': 'result',
                   'reviewer_id': 'first', 'author_id': 'author',
                   'context': {'prior_assignment_id': first['id']}}
        revision = self.case.store.revision
        roots = []
        original_init = Evaluation.__init__

        def initialize(evaluation, records, artifacts, *, _owner=None):
            original_init(evaluation, records, artifacts, _owner=_owner)
            if _owner is None:
                roots.append(evaluation)

        with tempfile.TemporaryDirectory() as directory:
            baseline = Store(shutil.copytree(self.case.root, Path(directory) / 'baseline'))
            with patch.object(Evaluation, '__init__', initialize):
                actual = protocol.record_assignment(self.case.store, payload, expected_revision=revision,
                                                    request_id='assign-part-2')['result']
            with patch.object(Evaluation, 'of', side_effect=Evaluation):
                expected = protocol.record_assignment(baseline, payload, expected_revision=revision,
                                                      request_id='assign-part-2')['result']
            self.assertEqual(actual, expected)
            self.assertEqual(self.case.read_records()['review_assignment']['part-2'], actual)
            self.assertEqual(baseline.snapshot()['records']['review_assignment']['part-2'], expected)
            packet = self.case.artifacts.read(actual['packet'])
            self.assertEqual(packet, baseline.build_artifact_store().read(expected['packet']))
        self.assertEqual((json.loads(packet)[KEY]['stage'], json.loads(packet)[KEY]['turn']), ('part', 2))
        self.assertEqual(len(roots), 1)


class AssignmentRequestOnceTests(ResultPartsCase):
    def setUp(self):
        super().setUp()
        self.records = self.case.read_records()
        self.assignment = self.records['review_assignment']['bar-first']
        self.evaluation = Evaluation(self.records, self.case.artifacts)

    def test_repeated_request_is_built_once_and_returns_independent_copies(self):
        with patch.object(transport, 'list_built_requests', wraps=transport.list_built_requests) as build:
            first = protocol.build_assignment_request(self.records, self.evaluation, self.assignment)
            second = protocol.build_assignment_request(self.records, self.evaluation, self.assignment)
            self.assertEqual(build.call_count, 1)
            self.assertEqual(first, second)
            self.assertIsNot(first, second)
            expected = copy.deepcopy(second)
            first['input'][-1]['content'] = 'Changed by the caller.'
            self.assertEqual(second, expected)
            self.assertEqual(protocol.build_assignment_request(self.records, self.evaluation, self.assignment), expected)
            self.assertEqual(build.call_count, 1)

    def test_another_records_view_builds_again(self):
        other_records = copy.deepcopy(self.records)
        with patch.object(transport, 'list_built_requests', wraps=transport.list_built_requests) as build:
            first = protocol.build_assignment_request(self.records, self.evaluation, self.assignment)
            second = protocol.build_assignment_request(other_records, self.evaluation,
                                                       other_records['review_assignment'][self.assignment['id']])
        self.assertEqual(build.call_count, 2)
        self.assertEqual(first, second)
        self.assertIsNot(first, second)

    def test_records_views_select_their_own_current_or_recorded_fit(self):
        attempt = self.records['review_attempt']['attempt-' + self.assignment['id']]
        recorded = json.loads(self.case.artifacts.read(attempt['request']))
        current = copy.deepcopy(recorded)
        current['input'][-1]['content'] = 'The current fit differs from the recorded fit.'
        earlier_records = copy.deepcopy(self.records)
        earlier_records['review_attempt'] = {}
        earlier_evaluation = Evaluation(earlier_records, self.evaluation)
        views = ((earlier_records, earlier_evaluation, current), (self.records, self.evaluation, recorded))
        with patch.object(transport, 'list_built_requests',
                          side_effect=lambda *args: iter(copy.deepcopy((current, recorded)))) as build:
            for records, evaluation, expected in views + views[::-1]:
                assignment = records['review_assignment'][self.assignment['id']]
                self.assertEqual(protocol.build_assignment_request(records, evaluation, assignment), expected)
            self.assertEqual(build.call_count, 2)

    def test_changing_input_history_does_not_change_the_memoized_request(self):
        self.records['review_attempt'] = {}
        self.assignment['history'] = [
            {'role': 'user', 'content': 'An earlier supplied question.'},
            {'role': 'assistant', 'content': 'An earlier canonical assessment.'}]
        first = protocol.build_assignment_request(self.records, self.evaluation, self.assignment)
        expected = copy.deepcopy(first)
        self.assertEqual(first['input'][:2], self.assignment['history'])
        self.assignment['history'][0]['content'] = 'Changed by the input owner.'
        self.assignment['history'][1]['content'] = 'A substituted assessment.'
        first['input'][0]['content'] = 'Changed by the caller.'
        self.assertEqual(protocol.build_assignment_request(self.records, self.evaluation, self.assignment), expected)

    def test_request_matches_the_recorded_request_and_direct_rebuild(self):
        attempt = self.records['review_attempt']['attempt-' + self.assignment['id']]
        recorded = json.loads(self.case.artifacts.read(attempt['request']))
        packet = json.loads(self.case.artifacts.read(self.assignment['packet']))
        rebuilt = next(transport.list_built_requests(self.records['review_route'][self.assignment['route_id']],
                       self.assignment['prompt']['text'], packet, self.assignment.get('history', ())))
        request = protocol.build_assignment_request(self.records, self.evaluation, self.assignment)
        self.assertEqual(request, recorded)
        self.assertEqual(request, rebuilt)

    def test_completed_attempts_are_indexed_once_for_multiple_assignments(self):
        attempts = self.records['review_attempt']
        observed_attempts = Mock(wraps=attempts)
        self.records['review_attempt'] = observed_attempts
        for identifier in ('bar-first', 'bar-second'):
            assignment = self.records['review_assignment'][identifier]
            attempt = attempts['attempt-' + identifier]
            expected = json.loads(self.case.artifacts.read(attempt['request']))
            self.assertEqual(protocol.build_assignment_request(self.records, self.evaluation, assignment), expected)
        self.assertEqual(observed_attempts.values.call_count, 1)

    def test_recorded_request_from_an_earlier_fit_order_is_retained(self):
        attempt = self.records['review_attempt']['attempt-' + self.assignment['id']]
        recorded = json.loads(self.case.artifacts.read(attempt['request']))
        current = copy.deepcopy(recorded)
        current['input'][-1]['content'] = 'The current fit differs from the recorded fit.'
        with patch.object(transport, 'list_built_requests', return_value=iter((current, recorded))):
            self.assertEqual(protocol.build_assignment_request(self.records, self.evaluation, self.assignment), recorded)

    def test_only_completed_attempts_with_a_request_select_an_earlier_fit(self):
        attempt = self.records['review_attempt']['attempt-' + self.assignment['id']]
        recorded = json.loads(self.case.artifacts.read(attempt['request']))
        current = copy.deepcopy(recorded)
        current['input'][-1]['content'] = 'The current fit differs from the recorded fit.'
        other_attempt = self.records['review_attempt']['attempt-bar-second']
        for status, request in (('failed', attempt['request']), ('completed', None), ('completed', {})):
            with self.subTest(status=status, request=request):
                records = copy.deepcopy(self.records)
                records['review_attempt'] = {attempt['id']: dict(attempt, status=status, request=request),
                                             other_attempt['id']: dict(other_attempt, request=attempt['request'])}
                with patch.object(transport, 'list_built_requests', return_value=iter((current, recorded))):
                    self.assertEqual(protocol.build_assignment_request(records, self.evaluation, self.assignment), current)

    def test_another_assignments_attempt_without_status_is_ignored(self):
        self.records['review_attempt']['attempt-bar-second'].pop('status')
        attempt = self.records['review_attempt']['attempt-' + self.assignment['id']]
        expected = json.loads(self.case.artifacts.read(attempt['request']))
        self.assertEqual(protocol.build_assignment_request(self.records, self.evaluation, self.assignment), expected)

    def test_every_attempt_requires_assignment_id_before_status_or_request(self):
        for fields in ({}, {'status': 'failed'}, {'status': 'completed'}):
            with self.subTest(fields=fields):
                records = copy.deepcopy(self.records)
                records['review_attempt']['malformed-attempt'] = dict(fields, id='malformed-attempt')
                with self.assertRaises(KeyError) as failure:
                    protocol.build_assignment_request(records, self.evaluation, self.assignment)
                self.assertEqual(failure.exception.args, ('assignment_id',))


class VisualAssignmentRequestTests(unittest.TestCase):
    def setUp(self):
        from test_visual_context_repair import VisualContextRepairTests
        self.fixture = VisualContextRepairTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.assignment = self.fixture.assign()
        self.records = self.fixture.case.read_records()
        self.evaluation = Evaluation(self.records, self.fixture.artifacts)

    def test_visual_request_is_built_on_every_call(self):
        with patch.object(transport, 'list_built_requests', wraps=transport.list_built_requests) as build:
            first = protocol.build_assignment_request(self.records, self.evaluation, self.assignment)
            second = protocol.build_assignment_request(self.records, self.evaluation, self.assignment)
        self.assertEqual(first, second)
        self.assertEqual(build.call_count, 2)

    def test_changed_emitter_rejects_a_warm_visual_request_and_restoring_it_recovers(self):
        from research_harness import review_exact_text_cost_v1 as emitter
        route = self.records['review_route'][self.assignment['route_id']]
        packet = json.loads(self.fixture.artifacts.read(self.assignment['packet']))
        original = Path(emitter.__file__).read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'review_exact_text_cost_v1.py'
            source.write_bytes(original)
            with patch.object(emitter, '__file__', str(source)):
                first = protocol.build_assignment_request(self.records, self.evaluation, self.assignment)
                source.write_bytes(original + b'\n# A changed native emitter implementation.\n')
                with self.assertRaises(ResearchError) as direct_failure:
                    next(transport.list_built_requests(route, self.assignment['prompt']['text'], packet,
                                                       self.assignment.get('history', ())))
                self.assertEqual(direct_failure.exception.code, 'review_visual_repair_mismatch')
                with self.assertRaises(ResearchError) as failure:
                    protocol.build_assignment_request(self.records, self.evaluation, self.assignment)
                self.assertEqual(failure.exception.as_dict(), direct_failure.exception.as_dict())
                source.write_bytes(original)
                self.assertEqual(protocol.build_assignment_request(self.records, self.evaluation, self.assignment), first)
