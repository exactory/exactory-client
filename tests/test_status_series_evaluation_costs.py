"""Command-scoped readers reuse immutable series work within the arena budget."""

import copy
import json
from collections import Counter
from contextlib import nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from problem_parts_fixtures import ResultPartsCase
from research_harness import research_decisions as decisions
from research_harness import review_delivered_evidence as delivery
from research_harness import review_problem_parts as parts
from research_harness import review_protocol as protocol
from research_harness.evaluation import Evaluation
from research_harness.evidence import digest
from research_harness.review_state_evaluation import runtime_scope


class ArtifactParseCostTests(LiteratureCase):
    def test_unready_series_turns_do_not_read_observation_artifacts(self):
        assignments = [{'id': 'turn-' + str(index)} for index in range(3)]
        attempts = {row['id']: {'id': row['id'], 'origin': 'instrumented',
                               'request': protocol._put_json(self.artifacts, {'turn': row['id']})}
                    for row in assignments}
        records = {'review_attempt': attempts}
        evaluation = Evaluation(records, self.artifacts)
        with patch.object(parts, '_list_series', return_value=[(row, {}) for row in assignments]), \
                patch.object(protocol, 'assignment_state', side_effect=lambda records, artifacts, identifier:
                             {'ready': False, 'attempt_id': identifier}), \
                patch.object(self.artifacts, 'read', wraps=self.artifacts.read) as read:
            with runtime_scope(records, evaluation):
                self.assertEqual(parts.observe_series(records, evaluation, 'series'), [])
            self.assertEqual(read.call_count, 0)

    def test_command_readers_share_artifact_parses_across_derived_record_views(self):
        records = {'review_assignment': {}}
        references = []
        for index in range(3):
            reference = protocol._put_json(self.artifacts, {'series': index})
            records['review_assignment'][str(index)] = {'packet': reference}
            references.append(reference)
        evaluation = Evaluation(records, self.artifacts)
        with patch.object(protocol.json, 'loads', wraps=json.loads) as decode, \
                patch.object(self.artifacts, 'read', wraps=self.artifacts.read) as read:
            with runtime_scope(records, evaluation):
                for index in range(3):
                    view_records = dict(records)
                    view = Evaluation.of(view_records, evaluation)
                    self.assertEqual(view.once('view', lambda: index), index)
                    for reference in references:
                        protocol._read_json(view, reference)
            self.assertEqual((decode.call_count, read.call_count), (3, 3))

    def test_strict_history_json_does_not_reuse_a_permissive_duplicate_key_parse(self):
        from research_harness.errors import ResearchError
        reference = self.artifacts.put(b'{"value":1,"value":2}', 'application/json')
        evaluation = Evaluation({}, self.artifacts)
        self.assertEqual(evaluation.read_json(reference), {'value': 2})
        with self.assertRaises(ResearchError) as caught:
            evaluation.read_json(reference, strict=True)
        self.assertEqual(caught.exception.code, 'invalid_json')
        self.assertEqual(evaluation.read(reference), b'{"value":1,"value":2}')

    def test_parsed_and_raw_readers_share_the_exact_verified_artifact_under_pressure(self):
        raw = b'{ "series" : 1, "text" : "' + b'x' * 16000 + b'" }\n'
        reference = self.artifacts.put(raw, 'application/json')
        evaluation = Evaluation({}, self.artifacts, retention_budget_bytes=30000)
        with patch.object(self.artifacts, 'read', wraps=self.artifacts.read) as read:
            self.assertEqual(protocol._read_json(evaluation, reference)['series'], 1)
            evaluation.read(self.artifacts.put(b'pressure' * 2500, 'text/plain'))
            self.assertEqual(evaluation.read(reference), raw)
            self.assertEqual(protocol._read_json(evaluation, reference)['text'], 'x' * 16000)
            actual = [call for call in read.call_args_list if call.args[0] == reference]
            self.assertEqual(len(actual), 1)
        self.assertLessEqual(evaluation.retention_report()['optional_bytes'], 30000)

    def test_streaming_bytes_reverifies_evicted_parses_within_the_existing_budget(self):
        references = [protocol._put_json(self.artifacts, {'series': index, 'text': 'x' * 1000})
                      for index in range(3)]
        evaluation = Evaluation({}, self.artifacts, retention_budget_bytes=64000)
        with patch.object(protocol.json, 'loads', wraps=json.loads) as decode:
            for reference in references:
                protocol._read_json(evaluation, reference)
            for index in range(6):
                evaluation.read(self.artifacts.put(bytes([index]) * 40000, 'text/plain'))
            for index, reference in enumerate(references):
                self.assertEqual(protocol._read_json(evaluation, reference)['series'], index)
            self.assertEqual(decode.call_count, 6)
        self.assertLessEqual(evaluation.retention_report()['optional_bytes'], 64000)

    def test_large_values_do_not_evict_repeated_parses_from_the_bounded_arena(self):
        references = [protocol._put_json(self.artifacts, {'series': index, 'text': 'x' * 16000})
                      for index in range(3)]
        evaluation = Evaluation({}, self.artifacts, retention_budget_bytes=64000)
        with patch.object(protocol.json, 'loads', wraps=json.loads) as decode:
            for _ in range(3):
                for index, reference in enumerate(references):
                    self.assertEqual(protocol._read_json(evaluation, reference)['series'], index)
                    self.assertLessEqual(evaluation.retention_report()['optional_bytes'], 64000)
            self.assertEqual(decode.call_count, 3)

    def test_command_parses_each_artifact_once_and_returns_independent_values(self):
        records = {'review_assignment': {}}
        references = []
        for index in range(3):
            reference = protocol._put_json(self.artifacts, {'series': index, 'values': ['original']})
            records['review_assignment'][str(index)] = {'packet': reference}
            references.append(reference)
        evaluation = Evaluation(records, self.artifacts)
        with patch.object(protocol.json, 'loads', wraps=json.loads) as decode, \
                patch.object(self.artifacts, 'read', wraps=self.artifacts.read) as read:
            with runtime_scope(records, evaluation):
                for _ in range(3):
                    for index, reference in enumerate(references):
                        parsed = protocol._read_json(evaluation, reference)
                        self.assertEqual(parsed['values'], ['original'])
                        parsed['values'].clear()
                        packet = decisions.read_assignment_packet(records, evaluation, str(index))
                        self.assertEqual(packet['values'], ['original'])
            self.assertEqual(read.call_count, 3)
            self.assertEqual(decode.call_count, 3)

    def test_warm_parse_still_validates_the_full_artifact_descriptor(self):
        from research_harness.errors import ResearchError
        reference = protocol._put_json(self.artifacts, {'value': ['original']})
        evaluation = Evaluation({}, self.artifacts)
        protocol._read_json(evaluation, reference)
        for field, value in (('path', '../outside'), ('size', True), ('size', []),
                             ('sha256', 'invalid'), ('media_type', [])):
            with self.subTest(field=field, value=value):
                with self.assertRaises(ResearchError):
                    protocol._read_json(evaluation, dict(reference, **{field: value}))

    def test_zero_budget_and_new_command_reparse_and_reverify(self):
        reference = protocol._put_json(self.artifacts, {'value': ['original']})
        for budget, expected_reads in ((0, 3), (64 * 1024 * 1024, 1)):
            evaluation = Evaluation({}, self.artifacts, retention_budget_bytes=budget)
            with patch.object(protocol.json, 'loads', wraps=json.loads) as decode, \
                    patch.object(self.artifacts, 'read', wraps=self.artifacts.read) as read:
                for _ in range(3):
                    self.assertEqual(protocol._read_json(evaluation, reference), {'value': ['original']})
                self.assertEqual((decode.call_count, read.call_count), (expected_reads, expected_reads))
            self.assertLessEqual(evaluation.retention_report()['optional_bytes'], budget)
        with patch.object(self.artifacts, 'read', wraps=self.artifacts.read) as read:
            protocol._read_json(Evaluation({}, self.artifacts), reference)
            self.assertEqual(read.call_count, 1)


class LegacySeriesCostTests(ResultPartsCase):
    def setUp(self):
        super().setUp()
        self.oversized()
        self.series = []
        self.reviews = []
        for reviewer in ('first', 'second'):
            turns, final = self.finish_parts(reviewer=reviewer)
            self.reviews.append(self.record_final(final))
            self.series.append(turns + [final])
        self.records = self.case.read_records()
        self.evaluation = Evaluation(self.records, self.case.artifacts)

    def test_series_readers_materialize_each_selected_turn_once_per_command(self):
        with patch.object(parts, '_sent_packet', wraps=parts._sent_packet) as read:
            with runtime_scope(self.records, self.evaluation):
                for _ in range(3):
                    for assignments in self.series:
                        for index, assignment in enumerate(assignments):
                            packets = parts.collect_series_packets(
                                self.records, self.evaluation, assignment['id'])
                            self.assertEqual(len(packets), index + 1)
                        self.assertEqual(packets[-1][parts.KEY]['stage'], 'final')
            counts = Counter(call.args[2]['id'] for call in read.call_args_list)
            self.assertEqual(counts, Counter({row['id']: 1 for series in self.series for row in series}))

    def test_selected_delivery_reuses_the_authenticated_request_projection(self):
        with patch.object(protocol, 'read_raw_observation',
                          wraps=protocol.read_raw_observation) as read:
            with runtime_scope(self.records, self.evaluation):
                for _ in range(3):
                    for assignments in self.series:
                        result = delivery.read_selected_delivery(
                            self.records, self.evaluation, assignments[-1]['id'])
                        self.assertEqual(result['attempt_id'], 'attempt-' + assignments[-1]['id'])
                        result['coverage_witnesses']['packets'].clear()
                        repeated = delivery.read_selected_delivery(
                            self.records, self.evaluation, assignments[-1]['id'])
                        self.assertTrue(repeated['coverage_witnesses']['packets'])
            counts = Counter(call.args[2] for call in read.call_args_list)
            for assignments in self.series:
                self.assertEqual(counts['attempt-' + assignments[-1]['id']], 1)

    def test_repeated_problem_coverage_reuses_each_selected_delivery(self):
        expected = self.case.expected
        with patch.object(parts, 'covers_items', wraps=parts.covers_items) as read:
            with runtime_scope(self.records, self.evaluation):
                for _ in range(3):
                    result = decisions._assess_problem_coverage(
                        self.records, self.evaluation, self.reviews, expected, digest(expected))
                    self.assertEqual(result, [])
                changed = copy.deepcopy(expected)
                changed['claim']['statement'] = 'A distinct synthetic statement.'
                result = decisions._assess_problem_coverage(
                    self.records, self.evaluation, self.reviews, changed, digest(expected))
                self.assertEqual(len(result), 2)
                self.assertTrue(all('claim' in row['missing'] for row in result))
            self.assertEqual(read.call_count, len(parts.SPLIT_KEYS) * len(self.reviews) * 2)

    def test_streaming_artifacts_does_not_repeat_native_assignment_assessment(self):
        evaluation = Evaluation(self.records, self.case.artifacts, retention_budget_bytes=1000000)
        with patch.object(protocol, '_assignment_state', wraps=protocol._assignment_state) as read:
            with runtime_scope(self.records, evaluation):
                for assignments in self.series:
                    self.assertTrue(protocol.assignment_state(
                        self.records, evaluation, assignments[-1]['id'])['ready'])
                initial = Counter(call.args[2] for call in read.call_args_list)
                for index in range(3):
                    evaluation.read(self.case.artifacts.put(bytes([index]) * 600000, 'text/plain'))
                for assignments in self.series:
                    self.assertTrue(protocol.assignment_state(
                        self.records, evaluation, assignments[-1]['id'])['ready'])
                self.assertEqual(Counter(call.args[2] for call in read.call_args_list), initial)
        self.assertLessEqual(evaluation.retention_report()['optional_bytes'], 1000000)


class SourceProjectionCostTests(LiteratureCase):
    def test_staged_source_projection_keeps_native_snapshot_validation(self):
        from research_harness import review_transport as transport
        from test_review_source_capacity import source_packet
        from test_review_html_delivery import ROUTE
        assignment = dict(id='source-task', role='result', route_id='route',
                          prompt={'text': 'Review the captured synthetic sources.'},
                          packet=protocol._put_json(self.artifacts, source_packet()))
        records = {'review_route': {'route': ROUTE}, 'review_assignment': {'source-task': assignment}}
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 80000):
            request = protocol.build_assignment_request(records, self.artifacts, assignment)
            assignment['staged_review'] = {'kind': 'task', 'epoch_id': 'epoch', 'task_id': 'task'}
            evaluation = Evaluation(records, self.artifacts)
            with patch.object(protocol, 'build_assignment_request', return_value=request) as read:
                with runtime_scope(records, evaluation):
                    for index in range(2):
                        self.assertTrue(protocol.source_delivery_packet(
                            records, evaluation, assignment)['pending_source_requests'])
                self.assertEqual(read.call_count, 2)

    def test_command_views_share_identical_source_projection_inputs_and_separate_routes(self):
        from research_harness import review_transport as transport
        from test_review_source_capacity import source_packet
        from test_review_html_delivery import ROUTE
        records = {'review_route': {'route': ROUTE}, 'review_assignment': {}}
        for index in range(3):
            identifier = 'source-series-' + str(index)
            packet = dict(source_packet(), reviewer_id=identifier)
            records['review_assignment'][identifier] = dict(
                id=identifier, role='result', route_id='route',
                prompt={'text': 'Review the captured synthetic sources.'},
                packet=protocol._put_json(self.artifacts, packet))
        evaluation = Evaluation(records, self.artifacts)
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 80000), \
                patch.object(protocol, 'build_assignment_request',
                             wraps=protocol.build_assignment_request) as read:
            with runtime_scope(records, evaluation):
                for index in range(3):
                    view_records = dict(records)
                    view = Evaluation.of(view_records, evaluation)
                    for assignment in records['review_assignment'].values():
                        packet = protocol.source_delivery_packet(view_records, view, assignment)
                        self.assertTrue(packet['pending_source_requests'])
                        packet['source_deliveries'].clear()
                self.assertEqual(read.call_count, 3)
                route = copy.deepcopy(ROUTE)
                route['configuration']['max_output_tokens'] += 1
                changed = dict(records, review_route={'route': route})
                for index in range(2):
                    view_records = dict(changed)
                    view = Evaluation.of(view_records, evaluation)
                    for assignment in changed['review_assignment'].values():
                        packet = protocol.source_delivery_packet(view_records, view, assignment)
                        self.assertTrue(packet['source_deliveries'])
                self.assertEqual(read.call_count, 6)

    def test_shared_lineages_reconstruct_each_source_capacity_projection_once(self):
        from research_harness import review_transport as transport
        from test_review_source_capacity import source_packet
        from test_review_html_delivery import ROUTE
        records = {'review_route': {'route': ROUTE}, 'review_assignment': {}}
        for index in range(3):
            identifier = 'source-series-' + str(index)
            packet = dict(source_packet(), reviewer_id=identifier)
            records['review_assignment'][identifier] = dict(
                id=identifier, role='result', route_id='route',
                prompt={'text': 'Review the captured synthetic sources.'},
                packet=protocol._put_json(self.artifacts, packet))
        evaluation = Evaluation(records, self.artifacts)
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 80000), \
                patch.object(protocol, 'build_assignment_request',
                             wraps=protocol.build_assignment_request) as read:
            with runtime_scope(records, evaluation):
                for _ in range(3):
                    for assignment in records['review_assignment'].values():
                        packet = protocol.source_delivery_packet(records, evaluation, assignment)
                        self.assertTrue(packet['pending_source_requests'])
                        self.assertEqual(packet['reviewer_id'], assignment['id'])
                        packet['source_deliveries'].clear()
            counts = Counter(call.args[2]['id'] for call in read.call_args_list)
            self.assertEqual(counts, Counter({identifier: 1 for identifier in records['review_assignment']}))


class HistoryCostTests(LiteratureCase):
    def test_command_views_materialize_each_immutable_history_once(self):
        from research_harness import review_bodies as bodies
        assignments = []
        for index in range(3):
            assignment = {'id': 'turn-' + str(index), 'session_id': 'series-' + str(index)}
            assignment['history_ref'] = bodies.write_history(
                self.artifacts, assignment, [{'role': 'user', 'content': 'Synthetic history ' + str(index)}])
            assignments.append(assignment)
        records = {'review_assignment': {row['id']: row for row in assignments}}
        evaluation = Evaluation(records, self.artifacts)
        with patch.object(bodies, '_read_pages', wraps=bodies._read_pages) as read:
            with runtime_scope(records, evaluation):
                for index in range(3):
                    view = Evaluation.of(dict(records), evaluation)
                    for assignment in assignments:
                        history = bodies.read_history(view, assignment)
                        self.assertTrue(history[0]['content'].startswith('Synthetic history '))
                        history[0]['content'] = 'Caller mutation.'
            self.assertEqual(read.call_count, 3)

    def test_histories_share_one_parse_of_each_unchanged_prefix_page(self):
        from research_harness import review_bodies as bodies
        assignments = []
        messages = [{'role': 'user', 'content': 'Synthetic message ' + str(index)} for index in range(34)]
        for index in range(3):
            assignment = {'id': 'turn-' + str(index), 'session_id': 'series'}
            assignment['history_ref'] = bodies.write_history(
                self.artifacts, assignment, messages[:32 + index],
                previous=assignments[-1] if assignments else None)
            assignments.append(assignment)
        reference = assignments[0]['history_ref']['pages'][0]
        self.assertTrue(all(row['history_ref']['pages'][0] == reference for row in assignments))
        shared_page = self.artifacts.read(reference)
        evaluation = Evaluation({}, self.artifacts)
        with patch.object(protocol.json, 'loads', wraps=json.loads) as decode:
            for assignment in assignments:
                self.assertEqual(len(bodies.read_history(evaluation, assignment)),
                                 assignment['history_ref']['message_count'])
            self.assertEqual(sum(call.args[0] == shared_page for call in decode.call_args_list), 1)

    def test_shared_lineages_read_each_paged_history_once(self):
        from research_harness import review_bodies as bodies
        assignments = []
        for index in range(3):
            assignment = {'id': 'turn-' + str(index), 'session_id': 'series-' + str(index)}
            assignment['history_ref'] = bodies.write_history(self.artifacts, assignment, [
                {'role': 'user', 'content': 'A synthetic question.'},
                {'role': 'assistant', 'content': 'A synthetic response.'}])
            assignments.append(assignment)
        records = {'review_assignment': {row['id']: row for row in assignments}}
        evaluation = Evaluation(records, self.artifacts)
        with patch.object(bodies, '_read_pages', wraps=bodies._read_pages) as read:
            with runtime_scope(records, evaluation):
                for _ in range(3):
                    for assignment in assignments:
                        history = bodies.read_history(evaluation, assignment)
                        self.assertEqual(history[0]['content'], 'A synthetic question.')
                        history[0]['content'] = 'Caller mutation.'
            self.assertEqual(read.call_count, 3)


class DeliveryValidationTests(ResultPartsCase):
    def retain_delivery_while_streaming(self, records, evaluation, assignment_id):
        with patch.object(evaluation, 'reuse_value', wraps=evaluation.reuse_value) as retain:
            for _ in range(2):
                delivery.read_selected_delivery(records, evaluation, assignment_id)
            key = next(call.args[0] for call in retain.call_args_list
                       if call.args[0][0] == 'authenticated_delivery')
        for index in range(4):
            self.assertIsNotNone(evaluation.reuse_value(key))
            evaluation.read(self.case.artifacts.put(bytes([index]) * 300000, 'text/plain'))
            self.assertIsNotNone(evaluation.reuse_value(key))

    def test_retained_series_delivery_reverifies_evicted_prior_turn_artifacts(self):
        from research_harness.errors import ResearchError
        self.oversized()
        turns, final = self.finish_parts()
        self.record_final(final)
        records = self.case.read_records()
        evaluation = Evaluation(records, self.case.artifacts, retention_budget_bytes=1000000)
        reference = records['review_attempt']['attempt-' + turns[0]['id']]['request']
        with runtime_scope(records, evaluation):
            self.retain_delivery_while_streaming(records, evaluation, final['id'])
            artifact_path = self.case.root / reference['path']
            artifact_path.chmod(0o600)
            artifact_path.write_bytes(b'corrupt')
            with self.assertRaises(ResearchError):
                delivery.read_selected_delivery(records, evaluation, final['id'])

    def test_retained_delivery_reverifies_an_evicted_observation_artifact(self):
        from research_harness.errors import ResearchError
        self.oversized()
        assignment = self.assign()
        self.invoke(assignment)
        records = self.case.read_records()
        evaluation = Evaluation(records, self.case.artifacts, retention_budget_bytes=1000000)
        reference = records['review_attempt']['attempt-' + assignment['id']]['request']
        with runtime_scope(records, evaluation):
            self.retain_delivery_while_streaming(records, evaluation, assignment['id'])
            artifact_path = self.case.root / reference['path']
            artifact_path.chmod(0o600)
            artifact_path.write_bytes(b'corrupt')
            with self.assertRaises(ResearchError):
                delivery.read_selected_delivery(records, evaluation, assignment['id'])


class CoverageRuntimeTests(unittest.TestCase):
    def test_changed_visual_emitter_invalidates_warm_coverage_across_command_scopes(self):
        import test_visual_context_repair as fixtures
        from research_harness import review_exact_text_cost_v1 as emitter
        from research_harness import review_transport as transport
        fixture = fixtures.VisualContextRepairTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        repaired = fixture.assign()
        packet = protocol._read_json(fixture.artifacts, repaired['packet'])
        response = copy.deepcopy(fixture.output)
        response['reassessment'] = {'assignment_id': fixture.prior['id'], 'findings': [
            {'review_id': row['id'], 'disposition': 'revised',
             'reason': 'The exact synthetic source supports this assessment.',
             'evidence': [{'kind': 'source', 'link': fixture.fixture.pdf_link}]}
            for row in packet['historical_reviews']]}
        with patch.object(transport, 'send_request', side_effect=lambda route, request, credential:
                          fixture.fixture.response(route, request, response)):
            fixture.case.mutate(protocol.invoke_assignment, {'id': 'coverage-repair-attempt',
                'assignment_id': repaired['id']}, credential='test-only')
        records = fixture.case.read_records()
        reviews = [{'id': 'review', 'assignment_id': repaired['id']}]
        expected = fixture.case.expected
        original = Path(emitter.__file__).read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'emitter.py'
            with patch.object(emitter, '__file__', str(source)):
                for scoped in (False, True):
                    with self.subTest(scoped=scoped):
                        source.write_bytes(original)
                        evaluation = Evaluation(records, fixture.artifacts)
                        with runtime_scope(records, evaluation) if scoped else nullcontext():
                            self.assertEqual(decisions._assess_problem_coverage(
                                records, evaluation, reviews, expected, digest(expected)), [])
                        source.write_bytes(original + b'\n# A changed synthetic emitter.\n')
                        with runtime_scope(records, evaluation) if scoped else nullcontext():
                            result = decisions._assess_problem_coverage(
                                records, evaluation, reviews, expected, digest(expected))
                        self.assertTrue(result)
                        self.assertEqual(result[0]['code'], 'problem_evidence_unassessed')
                        self.assertEqual(result[0]['issues'][0]['code'], 'review_invocation_unverified')
