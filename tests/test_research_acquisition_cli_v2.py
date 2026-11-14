"""Admission, replay, operation exclusion and persistent query ceiling regressions."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research_harness import acquisition, cli
from research_harness.errors import ResearchError
from research_harness.import_io import MAX_IMPORT_RESPONSE_BYTES, MAX_IMPORT_PAYLOAD_BYTES
from research_harness.storage import Store
from research_fixtures import client, xml_response, atom, entry, crossref, json_response


class AcquisitionV2Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, HOME=str(self.root), XDG_STATE_HOME=str(self.root / 'state'))
        environment.start()
        self.addCleanup(environment.stop)
        self.store = Store(self.root, create=True)

    def import_values(self):
        return dict(source_url='https://api.crossref.org/works/10.1234/example', captured_at='2026-01-01T00:00:00Z',
                    request_id='import', expected_revision=0)

    def test_historical_oversize_import_replays_before_parsing_and_changed_payload_conflicts(self):
        response = json.dumps(crossref()).encode() + b' ' * MAX_IMPORT_RESPONSE_BYTES
        values = self.import_values()
        # Seed the authentic legacy command before the new admission policy applies.
        with patch('research_harness.import_io.MAX_IMPORT_RESPONSE_BYTES', len(response) + 1):
            result = acquisition.import_response(self.store, 'crossref', response, **values)
        with patch('research_harness.acquisition._import_page', side_effect=AssertionError('Replay reparsed')):
            self.assertEqual(acquisition.import_response(self.store, 'crossref', response, **values), result)
            for changes in ({'response': response + b' '}, {'media_type': 'text/plain'},
                            {'source_url': 'https://example.org/other'}, {'mappings': []}):
                args = dict(values, response=response)
                args.update(changes)
                with self.assertRaises(ResearchError) as caught:
                    acquisition.import_response(self.store, 'crossref', **args)
                self.assertEqual(caught.exception.code, 'request_id_conflict')

    def test_new_oversize_python_response_refused_before_parse_without_writes(self):
        before = self.store.snapshot()
        with patch('research_harness.acquisition._import_page', side_effect=AssertionError('Oversize parsed')):
            with self.assertRaises(ResearchError) as caught:
                acquisition.import_response(self.store, 'crossref', b'x' * (MAX_IMPORT_RESPONSE_BYTES + 1), **self.import_values())
        self.assertEqual(caught.exception.code, 'import_input_too_large')
        self.assertEqual(self.store.snapshot(), before)

    def test_new_oversize_cli_payload_refused_before_json(self):
        path = self.root / 'payload.json'
        path.write_bytes(b' ' * (MAX_IMPORT_PAYLOAD_BYTES + 1))
        args = cli.build_parser().parse_args(['import-response', '--workspace', str(self.root), '--file', str(path),
                                              '--request-id', 'new', '--expected-revision', '0'])
        with patch('research_harness.cli.strict_json', side_effect=AssertionError('Oversize payload parsed')):
            with self.assertRaises(ResearchError) as caught:
                cli.run(args)
        self.assertEqual(caught.exception.code, 'import_input_too_large')
        self.assertEqual(self.store.revision, 0)

    def test_cli_response_bounded_and_historical_file_replay_streamed(self):
        path = self.root / 'response.json'
        path.write_bytes(json.dumps(crossref()).encode() + b' ' * MAX_IMPORT_RESPONSE_BYTES)
        payload = dict(provider='crossref', response_file=str(path), **self.import_values())
        payload.pop('request_id'); payload.pop('expected_revision')
        with patch('research_harness.acquisition._import_page', side_effect=AssertionError('Oversize response parsed')):
            with self.assertRaises(ResearchError) as caught:
                cli.acquisition_command(self.store, 'import-response', payload,
                                        dict(request_id='new', expected_revision=0))
        self.assertEqual(caught.exception.code, 'import_input_too_large')
        with patch('research_harness.import_io.MAX_IMPORT_RESPONSE_BYTES', path.stat().st_size + 1):
            original = acquisition.import_response(self.store, 'crossref', path.read_bytes(), **self.import_values())
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('Unbounded replay read')), \
                patch('research_harness.acquisition._import_page', side_effect=AssertionError('Replay parsed')):
            replay = cli.acquisition_command(self.store, 'import-response', payload,
                                            dict(request_id='import', expected_revision=0))
        self.assertEqual(replay, original)

    def test_concurrent_acquisition_cannot_supersede_live_operation(self):
        outcomes = []
        http, _, _ = client([xml_response(atom([entry()], total=1))])
        real_get = http.get
        def acquire_inside(*args, **kwargs):
            other, _, _ = client([xml_response(atom([entry()], total=1))])
            try:
                acquisition.acquire_work(Store(self.root), '2601.00001v1', request_id='second',
                                         expected_revision=self.store.revision, http=other)
            except ResearchError as error:
                outcomes.append(error.code)
            return real_get(*args, **kwargs)
        with patch.object(http, 'get', side_effect=acquire_inside):
            result = acquisition.acquire_work(self.store, '2601.00001v1', request_id='first', expected_revision=0, http=http)
        self.assertEqual(outcomes, ['acquisition_in_progress'])
        self.assertEqual(result['status'], 'complete')
        self.assertIsNone(self.store.committed_request('second'))

    def test_minimum_query_ceiling_persists_and_explicit_recheck_fetches(self):
        definition = dict(corpus='arxiv', primaryCategory='cs.LG', windowStart='2026-01-01', windowEnd='2026-01-01')
        http, _, _ = client([])
        with patch('research_harness.acquisition._run_collection', return_value={}):
            acquisition.collect_cohort(self.store, definition, request_id='collect', expected_revision=0, http=http)
        collection = next(iter(self.store.snapshot()['records']['collection'].values()))
        collection['partitions'][0].update(start='202601010000', end='202601010000')
        self.store.mutate('fixture.minute', {}, lambda tx: tx.put('collection', collection['id'], collection),
                          request_id='minute', expected_revision=self.store.revision)
        http, wire, _ = client([xml_response(atom(total=10001))])
        first = acquisition.resume_cohort(self.store, collection['id'], request_id='first',
                                         expected_revision=self.store.revision, http=http, max_requests=1)
        diagnosis = first['pending'][0]
        self.assertEqual(diagnosis['code'], 'query_ceiling')
        self.assertFalse(diagnosis['retryable_with_same_configuration'])
        self.assertEqual(diagnosis['adapter_ceiling'], 10000)
        empty, wire, _ = client([])
        again = acquisition.resume_cohort(self.store, collection['id'], request_id='again',
                                         expected_revision=self.store.revision, http=empty, max_requests=1)
        self.assertEqual(wire.requests, [])
        self.assertEqual(again['pending'], first['pending'])
        http, wire, _ = client([xml_response(atom(total=10001))])
        acquisition.resume_cohort(self.store, collection['id'], request_id='recheck', expected_revision=self.store.revision,
                                  http=http, max_requests=1, recheck=True)
        self.assertEqual(len(wire.requests), 1)

    def test_fulltext_commands_refuse_new_checks_before_tools_or_writes(self):
        for command in ('fulltext-start', 'fulltext-resume'):
            path = self.root / 'payload.json'
            path.write_text('{}')
            args = cli.build_parser().parse_args([command, '--workspace', str(self.root), '--file', str(path),
                '--request-id', command, '--expected-revision', '0', '--check'])
            with self.assertRaises(ResearchError) as caught:
                cli.run(args)
            self.assertEqual(caught.exception.code, 'check_unsupported')
        self.assertEqual(self.store.revision, 0)

    def test_new_oversize_mapped_response_refused_before_json(self):
        values = dict(self.import_values(), mappings=[])
        with patch('research_harness.imports._json', side_effect=AssertionError('Oversize mapped JSON parsed')):
            with self.assertRaises(ResearchError) as caught:
                acquisition.import_response(self.store, 'web', b'x' * (MAX_IMPORT_RESPONSE_BYTES + 1),
                                            media_type='application/json', **values)
        self.assertEqual(caught.exception.code, 'import_input_too_large')

    def test_python_payload_admission_precedes_response_parse(self):
        values = dict(self.import_values(), mappings=[{'note': 'x' * (MAX_IMPORT_PAYLOAD_BYTES + 1)}])
        with patch('research_harness.acquisition._import_page', side_effect=AssertionError('Oversize payload parsed')):
            with self.assertRaises(ResearchError) as caught:
                acquisition.import_response(self.store, 'web', b'{}', media_type='application/json', **values)
        self.assertEqual(caught.exception.code, 'import_input_too_large')

    def test_ceiling_at_exhausted_cursor_rechecks_at_an_allowed_cursor(self):
        definition = dict(corpus='arxiv', primaryCategory='cs.LG', windowStart='2026-01-01', windowEnd='2026-01-01')
        with patch('research_harness.acquisition._run_collection', return_value={}):
            acquisition.collect_cohort(self.store, definition, request_id='collect', expected_revision=0, http=client([])[0])
        collection = next(iter(self.store.snapshot()['records']['collection'].values()))
        collection['partitions'][0].update(start='202601010000', end='202601010000', offset=10000, total=10001)
        collection['pending'] = [{'code': 'query_ceiling', 'partition_id': '0', 'reported_total': 10001}]
        self.store.mutate('fixture.ceiling', {}, lambda tx: tx.put('collection', collection['id'], collection),
                          request_id='cursor', expected_revision=self.store.revision)
        http, wire, _ = client([])
        preserved = acquisition.resume_cohort(self.store, collection['id'], request_id='keep',
                                              expected_revision=self.store.revision, http=http)
        self.assertEqual(wire.requests, [])
        self.assertFalse(preserved['pending'][0]['retryable_with_same_configuration'])
        http, wire, _ = client([xml_response(atom(total=10001))])
        acquisition.resume_cohort(self.store, collection['id'], request_id='recheck',
                                  expected_revision=self.store.revision, http=http, max_requests=1, recheck=True)
        self.assertEqual(len(wire.requests), 1)
        self.assertIn('start=0', wire.requests[0][0])

    def test_oversize_historical_command_payload_replays(self):
        response = json.dumps(crossref()).encode()
        values = dict(self.import_values(), mappings=[{'legacy': 'x' * MAX_IMPORT_PAYLOAD_BYTES}])
        with patch('research_harness.import_io.MAX_IMPORT_PAYLOAD_BYTES', MAX_IMPORT_PAYLOAD_BYTES * 2):
            original = acquisition.import_response(self.store, 'crossref', response, **values)
        response_path = self.root / 'response.json'
        response_path.write_bytes(response)
        command = dict(provider='crossref', response_file=str(response_path), source_url=values['source_url'],
                       captured_at=values['captured_at'], mappings=values['mappings'])
        path = self.root / 'command.json'
        path.write_text(json.dumps(command))
        args = cli.build_parser().parse_args(['import-response', '--workspace', str(self.root), '--file', str(path),
                                              '--request-id', 'import', '--expected-revision', '0'])
        with patch('research_harness.acquisition._import_page', side_effect=AssertionError('Historical payload reparsed')):
            self.assertEqual(cli.run(args), original)

    def test_oversize_python_import_does_not_create_operation_files(self):
        before = {str(path.relative_to(self.root)) for path in self.root.rglob('*')}
        with self.assertRaises(ResearchError) as caught:
            acquisition.import_response(self.store, 'crossref', b'x' * (MAX_IMPORT_RESPONSE_BYTES + 1), **self.import_values())
        self.assertEqual(caught.exception.code, 'import_input_too_large')
        self.assertEqual({str(path.relative_to(self.root)) for path in self.root.rglob('*')}, before)

    def test_oversize_response_cannot_initialize_a_new_cli_store(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'workspace'
            target.mkdir()
            response = target / 'response'
            response.write_bytes(b'x' * (MAX_IMPORT_RESPONSE_BYTES + 1))
            payload = dict(provider='crossref', response_file=str(response), source_url=self.import_values()['source_url'],
                           captured_at=self.import_values()['captured_at'])
            path = Path(directory) / 'payload.json'
            path.write_text(json.dumps(payload))
            args = cli.build_parser().parse_args(['import-response', '--workspace', str(target), '--file', str(path),
                                                 '--request-id', 'new', '--expected-revision', '0'])
            with self.assertRaises(ResearchError) as caught:
                cli.run(args)
            self.assertEqual(caught.exception.code, 'import_input_too_large')
            self.assertEqual(list(target.iterdir()), [response])
