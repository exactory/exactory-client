"""Disposable historical fixtures and bounded real-series execution, offline."""

import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import shutil
import tempfile
import unittest
from unittest.mock import patch

from research_harness import record_check, review_series, review_transport
from research_harness.errors import ResearchError
from research_harness.storage import Store
import test_review_continuation as continuation_fixtures
from tools.review_measurement import run_live


def digest_tree(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


class HistoricalFixtureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source'
        store = Store(self.source, create=True)
        self.artifact = store.build_artifact_store().put(b'Synthetic retained evidence.', 'text/plain')
        store.mutate('fixture.before_post', {}, lambda tx: tx.put(
            'fixture', 'before', dict(id='before', artifact=self.artifact)),
            expected_revision=0, request_id='before')
        store.mutate('fixture.post', {}, lambda tx: tx.put(
            'problem_post', 'posted', dict(id='posted', statement_id='statement')),
            expected_revision=1, request_id='posted')

    def test_prefix_preserves_exact_events_receipts_and_artifacts_without_source_writes(self):
        before = digest_tree(self.source)
        connect = sqlite3.connect
        def refuse_source(path, *args, **kwargs):
            self.assertNotIn(str(self.source), str(path))
            return connect(path, *args, **kwargs)
        with patch.object(sqlite3, 'connect', side_effect=refuse_source):
            fixture = run_live.build_fixture(self.source, 1, self.root / 'output')
        store = Store(fixture)
        self.assertEqual(store.revision, 1)
        self.assertNotIn('problem_post', store.snapshot()['records'])
        self.assertEqual(store.build_artifact_store().read(self.artifact), b'Synthetic retained evidence.')
        source_store = Store(self.source)
        with source_store._connection() as original, store._connection() as actual:
            for table in ('events', 'receipts'):
                self.assertEqual([tuple(row) for row in actual.execute('SELECT * FROM ' + table)],
                                 [tuple(row) for row in original.execute(
                                     'SELECT * FROM ' + table + ' WHERE revision <= 1')])
        self.assertEqual(digest_tree(self.source), before)
        self.assertEqual(source_store.revision, 2)

    def test_source_overlap_is_refused_before_writing(self):
        before = digest_tree(self.source)
        for output in (self.source, self.source / 'results', self.root):
            with self.subTest(output=output), self.assertRaises(ResearchError) as caught:
                run_live.build_fixture(self.source, 1, output)
            self.assertEqual(caught.exception.code, 'measurement_unsafe_path')
        self.assertEqual(digest_tree(self.source), before)

    def test_source_or_output_symlink_cannot_escape_into_source(self):
        output = self.root / 'output'
        output.mkdir()
        (output / 'fixture').symlink_to(self.source, target_is_directory=True)
        before = digest_tree(self.source)
        with self.assertRaises(ResearchError):
            run_live.build_fixture(self.source, 1, output)
        self.assertEqual(digest_tree(self.source), before)
        (self.source / 'outside').symlink_to(self.root / 'external')
        with self.assertRaises(ResearchError):
            run_live.build_fixture(self.source, 1, self.root / 'other-output')

    def test_invalid_revision_and_corrupt_history_are_refused(self):
        for revision in (-1, 3, True):
            with self.subTest(revision=revision), self.assertRaises(ResearchError):
                run_live.build_fixture(self.source, revision, self.root / ('out-' + str(revision)))
        database = self.source / '.exactory' / 'research.sqlite3'
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE records SET digest = ?", ('0' * 64,))
        with self.assertRaises(ResearchError) as caught:
            run_live.build_fixture(self.source, 1, self.root / 'corrupt-output')
        self.assertEqual(caught.exception.code, 'corrupt_state')


    def test_fixture_symlink_replacement_cannot_overwrite_source_database(self):
        before = digest_tree(self.source)
        original_copy = record_check.copy_snapshot
        def replace_after_copy(source, destination):
            original_copy(source, destination)
            if Path(destination).name == 'fixture':
                shutil.rmtree(destination)
                Path(destination).symlink_to(self.source, target_is_directory=True)
        with patch.object(record_check, 'copy_snapshot', side_effect=replace_after_copy), \
                self.assertRaises(ResearchError):
            run_live.build_fixture(self.source, 1, self.root / 'race-output')
        self.assertEqual(digest_tree(self.source), before)
        self.assertEqual(Store(self.source).revision, 2)


    def test_output_symlink_replacement_cannot_write_metadata_into_source(self):
        before = digest_tree(self.source)
        output = self.root / 'metadata-output'
        real_write = run_live.write_json
        def replace_before_write(path, value):
            if Path(path).name == 'fixture.json':
                output.rename(self.root / 'retained-output')
                output.symlink_to(self.source, target_is_directory=True)
            return real_write(path, value)
        with patch.object(run_live, 'write_json', side_effect=replace_before_write), \
                self.assertRaises(ResearchError):
            run_live.build_fixture(self.source, 1, output)
        self.assertEqual(digest_tree(self.source), before)


class LiveSeriesTests(unittest.TestCase):
    def setUp(self):
        fixture = continuation_fixtures.ContinuationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.case = fixture.case
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name) / 'campaign'
        self.revision = self.case.store.revision
        self.before = digest_tree(self.case.root)
        boundary = patch.object(review_transport, 'send_request',
                                side_effect=AssertionError('Tests cannot call a model.'))
        boundary.start()
        self.addCleanup(boundary.stop)
        # Exercise the actual candidate record-check in process, without requiring
        # the OS sandbox that the test runner itself cannot install.
        real_check = record_check.check_record
        qualification = patch.object(record_check, 'check_record', side_effect=lambda root, **kwargs:
                                     real_check(root, isolate=False))
        qualification.start()
        self.addCleanup(qualification.stop)

    def drive(self, case='synthetic', maximum=3):
        return run_live.run_live(case=case, source_copy=self.case.root,
            snapshot_revision=self.revision, assignment_id='bar-first',
            max_calls=maximum, output=self.output)

    def test_ready_fixture_runs_actual_series_and_preserves_sender_inputs(self):
        expected_route = copy.deepcopy(self.case.read_records()['review_route']['route'])
        from research_harness import review_protocol
        expected_request = review_protocol.build_assignment_request(self.case.read_records(),
            self.case.artifacts, self.case.read_records()['review_assignment']['bar-first'])
        calls = []
        def sender(route, request, credential):
            calls.append(copy.deepcopy((route, request)))
            return self.case.build_provider_response(self.case.build_review_output('bar'))
        real_series = review_series.run_series
        driven = []
        def series(store, head, **kwargs):
            driven.append((store.root, head))
            return real_series(store, head, **kwargs)
        with patch.object(review_transport, 'send_request', side_effect=sender), patch.object(
                review_series, 'run_series', side_effect=series):
            result = self.drive()
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['model_calls'], 1)
        self.assertEqual(len(driven), 1)
        self.assertEqual(calls, [(expected_route, expected_request)])
        self.assertTrue(Path(result['fixture']).is_relative_to(self.output.resolve()))
        records = Store(result['fixture']).snapshot()['records']
        self.assertIn('review:bar-first', records['value_review'])
        self.assertEqual(digest_tree(self.case.root), self.before)
        self.assertTrue(json.loads((self.output / 'cases/synthetic/record-check.json').read_text())['ready'])

    def test_staged_worker_launch_is_refused_inside_the_campaign(self):
        from research_harness import review_worker
        launched = []

        def series(store, head, **kwargs):
            review_worker.launch_worker(store, 'synthetic-reservation')
            launched.append(head)
            return 0
        with patch.object(review_series, 'run_series', side_effect=series), \
                self.assertRaises(ResearchError) as caught:
            self.drive(case='staged')
        self.assertEqual(caught.exception.code, 'measurement_worker_unsupported')
        self.assertEqual(launched, [])
        self.assertEqual(digest_tree(self.case.root), self.before)

    def test_record_check_must_be_ready_before_dispatch(self):
        for ready in (False, None):
            with self.subTest(ready=ready), patch.object(record_check, 'check_record',
                    return_value=dict(ready=ready)), self.assertRaises(ResearchError) as caught:
                self.drive(case='unready-' + str(ready))
            self.assertEqual(caught.exception.code, 'measurement_record_check_required')
        self.assertEqual(digest_tree(self.case.root), self.before)

    def test_cap_prevents_second_turn_dispatch_without_changing_route(self):
        output = self.case.build_review_output('bar')
        output['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
        output['value']['status'] = 'unresolved'
        calls = []
        def sender(route, request, credential):
            calls.append(copy.deepcopy(route))
            return self.case.build_provider_response(output)
        with patch.object(review_transport, 'send_request', side_effect=sender):
            result = self.drive(maximum=1)
        self.assertEqual(len(calls), 1)
        self.assertFalse(result['complete'])
        self.assertEqual(result['code'], 'measurement_budget_exhausted')
        self.assertEqual(result['model_calls'], 1)
        self.assertEqual(digest_tree(self.case.root), self.before)

    def test_usage_limit_stops_driver_before_wait_and_next_case(self):
        response = self.case.build_provider_response({})
        response['output'][0]['content'][0]['text'] = "You've hit your weekly limit"
        calls = []
        def sender(route, request, credential):
            calls.append(request)
            return response
        with patch.object(review_transport, 'send_request', side_effect=sender), patch.object(
                review_series.time, 'sleep', side_effect=AssertionError('Quota must stop before waiting')):
            result = self.drive()
            with self.assertRaises(ResearchError) as caught:
                self.drive(case='next-case')
        self.assertFalse(result['complete'])
        self.assertEqual(result['code'], 'measurement_usage_limit')
        self.assertEqual(caught.exception.code, 'measurement_usage_limit')
        self.assertEqual(len(calls), 1)
        self.assertFalse((self.output / 'cases/next-case').exists())
        self.assertEqual(digest_tree(self.case.root), self.before)

    def test_source_output_is_refused_without_dispatch(self):
        with self.assertRaises(ResearchError) as caught:
            run_live.run_live(case='unsafe', source_copy=self.case.root,
                snapshot_revision=self.revision, assignment_id='bar-first', max_calls=1,
                output=self.case.root / 'results')
        self.assertEqual(caught.exception.code, 'measurement_unsafe_path')
        self.assertEqual(digest_tree(self.case.root), self.before)

    def test_temporary_files_stay_under_output_and_credentials_are_never_copied(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            original_codex = root / 'original-codex'
            original_claude = root / 'original-claude'
            original_codex.mkdir()
            original_claude.mkdir()
            (original_codex / 'auth.json').write_text('{"synthetic": "auth"}')
            (original_claude / '.credentials.json').write_text('{"synthetic": "auth"}')
            with patch.dict(os.environ, {'CODEX_HOME': str(original_codex),
                                         'CLAUDE_CONFIG_DIR': str(original_claude)}):
                previous = dict(os.environ)
                with run_live.confine_temporary_files(root / 'output'):
                    self.assertTrue(Path(os.environ['TMPDIR']).is_relative_to(root / 'output'))
                    self.assertEqual(os.environ['CODEX_HOME'], str(original_codex))
                    self.assertEqual(os.environ['CLAUDE_CONFIG_DIR'], str(original_claude))
                    with tempfile.NamedTemporaryFile() as stream:
                        self.assertTrue(Path(stream.name).is_relative_to(root / 'output'))
                    self.assertEqual(list((root / 'output').rglob('auth.json')), [])
                    self.assertEqual(list((root / 'output').rglob('.credentials.json')), [])
                self.assertEqual(dict(os.environ), previous)


class LiveCasePreparationTests(unittest.TestCase):
    def test_focused_case_recreates_retained_payload_with_fixed_route(self):
        from tools.review_measurement import live_cases
        from test_review_parts_adjudication_journey import AdjudicationPartsCase
        fixture = AdjudicationPartsCase()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        case = fixture.case
        payload = dict(id='refused-primary', route_id='route', dossier_id=None,
                       role='adjudicator', reviewer_id='third', author_id='author',
                       context=copy.deepcopy(fixture.context))
        path = case.root / 'payloads/assignment-adjudication-primary-sources.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(payload))
        route = copy.deepcopy(case.read_records()['review_route']['route'])
        head = live_cases.prepare_assignment(case.store, 'quantum-focused-adjudication')
        assignment = case.read_records()['review_assignment'][head]
        self.assertEqual(assignment['context'], payload['context'])
        self.assertEqual(assignment['route_id'], 'route')
        self.assertNotEqual(head, payload['id'])
        self.assertEqual(case.read_records()['review_route']['route'], route)

    def test_openness_case_reexports_exact_spec_for_a_fresh_v5_chain(self):
        from tools.review_measurement import live_cases
        from openness_reading_fixtures import OpennessReadingCase
        fixture = OpennessReadingCase()
        fixture.reading_protocol = None
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        native = fixture.export(assignment_id='historical-open')
        payload = dict(id='historical-open', route_id='route', dossier_id=None,
                       role='native_math', reviewer_id='openness-reviewer', author_id='problem-author',
                       context=dict(native_packet=native))
        spec = dict(assignment_id='historical-open', reviewer_id='openness-reviewer',
                    minimum=dict(searches=[dict(formulation=fixture.selected['statement'],
                                               search_id=search['id']) for search in fixture.searches],
                                 settling_work_id=None, duplicate_resolutions=[]))
        for relative, value in (('payloads/assign-openness-5.json', payload),
                                ('notes/openness-spec-5.json', spec)):
            path = fixture.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value))
        fixture.limit_input()
        head = live_cases.prepare_assignment(fixture.store, 'bunkbed-openness-v5')
        assignment = fixture.store.snapshot()['records']['review_assignment'][head]
        fresh = json.loads(fixture.artifacts.read(assignment['context']['native_packet']))
        prior = json.loads(fixture.artifacts.read(native))
        self.assertEqual(fresh['scientific_context'], prior['scientific_context'])
        self.assertEqual(fresh['reviewer']['attestation_id'], 'review-assignment:' + head)
        packet = json.loads(fixture.artifacts.read(assignment['packet']))
        self.assertEqual(packet['reading_turn']['protocol'], 'exactory-openness-reading-v5')
        self.assertNotIn('prior_assignment_id', assignment['context'])


if __name__ == '__main__':
    unittest.main()
