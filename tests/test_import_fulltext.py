"""Supplied originals keep their bytes and reported origin."""

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research_harness import acquisition, cli, principles, problem_claims, source_links
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.record_check import check_authentication, check_integrity
from research_harness.storage import Store
from research_fixtures import atom, client, crossref, entry, json_response


ROOT = Path(__file__).resolve().parents[1]
ORIGIN = {'saved_by': 'A reader', 'obtained_from': 'https://publisher.example/survey.pdf',
          'obtained_at': '2026-11-12T16:30:00-08:00',
          'statement': 'Saved the complete publisher PDF using a browser.'}
PDF = b'%PDF-1.4\n% Synthetic survey original.\n%%EOF\n'
BODY = 'A synthetic survey of finite examples. References: none.\f'


class ImportFulltextTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / 'workspace'
        self.store = Store(self.root, create=True)
        self.artifacts = ArtifactStore(self.root)
        principles.initialize_research(self.store, {'profile': 'research', 'target': None,
            'preparation_policy': 'exhaustive-v1'}, expected_revision=0, request_id='init')
        http, _, _ = client([json_response(crossref())])
        acquisition.acquire_work(self.store, '10.1234/example', http=http,
            expected_revision=self.store.revision, request_id='metadata')
        (self.root / 'inputs').mkdir()
        self.file = self.root / 'inputs/survey.pdf'
        self.file.write_bytes(PDF)
        self.payload = {'identifier': 'doi:10.1234/example', 'file': 'inputs/survey.pdf',
                        'origin': copy.deepcopy(ORIGIN)}

    def import_text(self, payload=None, *, request_id='supplied', revision=None, **kwargs):
        operation = getattr(acquisition, 'import_fulltext', None)
        self.assertTrue(callable(operation), 'A saved full text needs an acquisition command')
        kwargs.setdefault('extractor', lambda data: {'status': 'extracted', 'text': BODY})
        return operation(self.store, **(self.payload if payload is None else payload),
            request_id=request_id, expected_revision=self.store.revision if revision is None else revision, **kwargs)

    def assert_refused(self, code, payload=None, **kwargs):
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            self.import_text(payload, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.store.snapshot(), before)

    def test_browser_saved_pdf_after_403_retains_original_origin_and_unverified_class(self):
        http, _, _ = client([(403, {'Content-Type': 'text/plain'}, b'Automated access refused.')], max_retries=0)
        refused = acquisition.acquire_fulltext(self.store, self.payload['identifier'], ORIGIN['obtained_from'],
            http=http, expected_revision=self.store.revision, request_id='publisher-fetch')
        self.assertEqual(refused['status'], 'pending')
        result = self.import_text(dict(self.payload, extraction_options={'layout': False}))
        self.assertEqual((result['status'], result['attempts_used']), ('complete', 0))
        capture = result['capture']
        source = self.store.snapshot()['records']['source'][capture['source_id']]
        self.assertEqual({key: source[key] for key in ('provider', 'capture_method', 'origin_verified',
            'imported', 'http_status', 'status', 'response_complete')},
            {'provider': 'fulltext', 'capture_method': 'supplied_file', 'origin_verified': False,
             'imported': True, 'http_status': None, 'status': 'captured', 'response_complete': True})
        self.assertEqual(self.artifacts.read(source['response']), PDF)
        self.assertEqual(source['response']['sha256'], hashlib.sha256(PDF).hexdigest())
        self.assertEqual(capture['original'], source['response'])
        self.assertEqual(self.artifacts.read(capture['text']).decode(), BODY)
        self.assertEqual(capture['extraction']['options'], {'layout': False})
        self.assertEqual({key: source['supplied'][key] for key in ORIGIN}, ORIGIN)
        self.assertEqual(source['supplied']['evidence'], 'reported_by_agent')
        note = source['supplied']['note']
        for phrase in ('person', 'did not fetch', 'did not verify', 'decisive'):
            self.assertIn(phrase, note)
        for use in source_links.CAPTURE_CLASSES_BY_USE:
            self.assertEqual(source_links.admits_capture_class(source, use), use in (
                'article_bundle', 'complete_original', 'session_source_delivery', 'openness_packet_source'), use)
        report = cli.status_report(self.store)
        row = report['supplied_texts'][0]
        self.assertEqual((row['version_id'], row['source_id']), (self.payload['identifier'], source['id']))
        self.assertEqual(row['supplied'], source['supplied'])
        self.assertEqual(row['original'], source['response'])
        self.assertNotIn('reading', self.store.snapshot()['records'])

    def test_utf8_text_and_all_three_identifier_classes_are_recorded(self):
        self.file = self.root / 'inputs/survey.txt'
        self.file.write_text('A complete synthetic café survey. References: none.', encoding='utf-8')
        for index, identifier in enumerate(('doi:10.1234/example', 'openalex:W123', 'url:https://publisher.example/work')):
            with self.subTest(identifier=identifier):
                if index:
                    raw = json.dumps({'id': identifier, 'title': 'A synthetic survey'}).encode()
                    acquisition.import_response(self.store, 'mcp', raw, source_url='https://metadata.example/work',
                        captured_at='2026-11-12T12:00:00Z', media_type='application/json',
                        mappings=[{'id': '/id', 'title': '/title'}], request_id='metadata-' + str(index),
                        expected_revision=self.store.revision)
                result = self.import_text(dict(self.payload, identifier=identifier, file='inputs/survey.txt'),
                                          request_id='text-' + str(index))
                capture = result['capture']
                self.assertEqual(capture['availability'], 'available')
                self.assertEqual(self.artifacts.read(capture['text']), self.file.read_bytes())
                self.assertEqual(capture['original']['media_type'], 'text/plain; charset=utf-8')
                self.assertEqual(capture['extraction']['media_type'], 'text/plain; charset=utf-8')
                self.assertEqual(capture['extraction']['text_bytes'], len(self.file.read_bytes()))

    def test_origin_requires_exact_fields_nonempty_text_public_url_and_zoned_time(self):
        cases = []
        for key in ORIGIN:
            for value in ('', '   ', None, [], 7):
                cases.append((dict(ORIGIN, **{key: value}), 'unsafe_url' if key == 'obtained_from' else 'invalid_input'))
            cases.append(({name: value for name, value in ORIGIN.items() if name != key}, 'invalid_input'))
        cases.extend([(dict(ORIGIN, extra='unreported'), 'invalid_input'),
                      (dict(ORIGIN, obtained_at='2026-11-12T16:30:00'), 'invalid_input'),
                      (dict(ORIGIN, obtained_from='ftp://publisher.example/work'), 'unsafe_url'),
                      (dict(ORIGIN, obtained_from='https://localhost/work'), 'unsafe_url'),
                      (dict(ORIGIN, obtained_from='https://user:secret@publisher.example/work'), 'unsafe_url')])
        for origin, code in cases:
            with self.subTest(origin=origin):
                self.assert_refused(code, dict(self.payload, origin=origin))

    def test_arxiv_and_unacquired_works_refuse_before_recording(self):
        self.assert_refused('invalid_input', dict(self.payload, identifier='arxiv:2601.00001v1'))
        self.assert_refused('unknown_work', dict(self.payload, identifier='doi:10.1234/missing'))

    def test_files_cannot_escape_or_traverse_symlinks_and_must_be_regular(self):
        (self.root / 'inputs/link.pdf').symlink_to(self.file)
        (self.root / 'linked').symlink_to(self.root / 'inputs', target_is_directory=True)
        os.mkfifo(str(self.root / 'inputs/pipe'))
        for path in ('../outside.pdf', str(self.root.parent / 'outside.pdf'),
                     'inputs/link.pdf', 'linked/survey.pdf'):
            with self.subTest(path=path):
                self.assert_refused('unsafe_path', dict(self.payload, file=path))
        for path in ('inputs', 'inputs/pipe', 'inputs/missing.pdf'):
            with self.subTest(path=path):
                self.assert_refused('invalid_input', dict(self.payload, file=path))
        result = self.import_text(dict(self.payload, file=str(self.file)))
        self.assertEqual(self.artifacts.read(result['capture']['original']), PDF)

    def test_html_and_non_utf8_binary_inputs_refuse_without_a_capture(self):
        cases = [('page.html', b'<article class="article-body"><p>A survey.</p></article>'),
                 ('denied.pdf', b'<!doctype html><html><body>Access refused.</body></html>'),
                 ('page.txt', b'<!doctype html><html><body>A survey.</body></html>'),
                 ('binary.txt', b'\xff\xfe\x00'), ('binary.dat', b'a\x00b')]
        for name, data in cases:
            with self.subTest(name=name):
                (self.root / 'inputs' / name).write_bytes(data)
                self.assert_refused('invalid_input', dict(self.payload, file='inputs/' + name))

    def test_pending_pdf_extraction_retains_original_and_origin_without_fulltext_credit(self):
        for index, status in enumerate(('extractor_missing', 'malformed_pdf', 'empty_text')):
            with self.subTest(status=status):
                result = self.import_text(request_id='pending-' + str(index),
                    extractor=lambda data: {'status': status, 'text': None})
                self.assertEqual(result['status'], 'pending')
                self.assertEqual(result['pending'], [{'code': status}])
                self.assertEqual(result['capture']['availability'], 'pending')
                self.assertIsNone(result['capture']['text'])
                self.assertEqual(self.artifacts.read(result['capture']['original']), PDF)
        self.assertEqual(len(cli.status_report(self.store)['supplied_texts']), 3)

    def test_empty_utf8_text_retains_the_original_with_an_empty_text_pending_state(self):
        (self.root / 'inputs/empty.txt').write_bytes(b'')
        result = self.import_text(dict(self.payload, file='inputs/empty.txt'))
        self.assertEqual((result['status'], result['pending']), ('pending', [{'code': 'empty_text'}]))
        self.assertIsNone(result['capture']['text'])
        self.assertEqual(self.artifacts.read(result['capture']['original']), b'')
        self.assertEqual(result['capture']['original']['sha256'],
                         'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')

    def test_pdf_extractor_refusal_finishes_a_pending_capture_with_the_original(self):
        def refuse(data):
            raise ResearchError('tool_timeout', 'Synthetic bounded extraction refusal')
        result = self.import_text(extractor=refuse)
        self.assertEqual((result['status'], result['pending']), ('pending', [{'code': 'tool_timeout'}]))
        self.assertEqual(self.artifacts.read(result['capture']['original']), PDF)
        self.assertIsNone(result['capture']['text'])
        operation = self.store.snapshot()['records']['acquisition_operation']['supplied']
        self.assertEqual(operation['state'], 'finished')

    def test_extraction_options_refuse_invalid_values_before_admission(self):
        for options in ([], {'layout': 'true'}, {'unknown': True}, {'layout': False, 'ocr': True}):
            with self.subTest(options=options):
                self.assert_refused('invalid_input', dict(self.payload, extraction_options=options))

    def test_oversized_input_refuses_before_acquisition_admission(self):
        operation = getattr(acquisition, 'import_fulltext', None)
        self.assertTrue(callable(operation), 'A saved full text needs an acquisition command')
        with patch.object(acquisition, 'DEFAULT_MAX_RESPONSE_BYTES', 16):
            self.assert_refused('import_input_too_large')

    def test_replay_is_authenticated_by_original_bytes_and_reported_origin(self):
        revision = self.store.revision
        result = self.import_text(revision=revision)
        self.assertEqual(self.import_text(revision=revision), result)
        self.assertEqual(len(self.store.snapshot()['records']['work'][self.payload['identifier']]['fulltexts']), 1)
        self.assert_refused('request_id_conflict', dict(self.payload, origin=dict(ORIGIN, saved_by='Another reader')),
                            revision=revision)
        self.file.write_bytes(PDF + b'changed')
        self.assert_refused('request_id_conflict', revision=revision)

    def test_cli_dispatch_example_and_problem_status_expose_supplied_texts(self):
        example = json.loads((ROOT / 'docs/research-cli-examples.json').read_text()).get('import-fulltext')
        self.assertIsNotNone(example, 'The new command needs an example payload')
        (self.root / 'inputs/survey.txt').write_text(BODY)
        payload = dict(self.payload, file='inputs/survey.txt')
        path = self.root / 'import.json'
        path.write_text(json.dumps(payload))
        args = cli.build_parser().parse_args(['import-fulltext', '--workspace', str(self.root),
            '--file', str(path), '--expected-revision', str(self.store.revision), '--request-id', 'cli-import'])
        result = cli.run(args)
        self.assertEqual(result['status'], 'complete')
        problem_claims.initialize_problem(self.store, {'slug': 'synthetic-survey', 'area': 'math.CO',
            'field': 'Finite examples', 'authors': ['synthetic-author']},
            expected_revision=self.store.revision, request_id='problem-init')
        self.assertEqual(cli.status_report(self.store)['supplied_texts'][0]['source_id'], result['capture']['source_id'])

    def test_record_check_requires_the_retained_origin_fixed_note_and_capture_labels(self):
        result = self.import_text()
        source_id = result['capture']['source_id']
        checked = check_authentication(self.store)
        self.assertIn(source_id, checked['supplied_texts'])
        self.assertGreater(check_integrity(self.store)['artifacts'], 0)
        source = self.store.snapshot()['records']['source'][source_id]
        damaged = []
        for key in ('note', 'evidence', 'saved_by', 'obtained_from', 'obtained_at', 'statement'):
            bad = copy.deepcopy(source)
            del bad['supplied'][key]
            damaged.append(bad)
        damaged.extend([dict(source, supplied={}), dict(source, origin_verified=True),
                        dict(source, capture_method='http'), dict(source, http_status=200)])
        for index, bad in enumerate(damaged):
            with self.subTest(index=index):
                self.store.mutate('fixture.damage', {}, lambda tx: tx.put('source', source_id, bad),
                    expected_revision=self.store.revision, request_id='damage-' + str(index))
                with self.assertRaises(ResearchError) as caught:
                    check_authentication(self.store)
                self.assertEqual(caught.exception.code, 'source_mismatch')
        self.store.mutate('fixture.restore', {}, lambda tx: tx.put('source', source_id, source),
            expected_revision=self.store.revision, request_id='restore')
        self.assertIn(source_id, check_authentication(self.store)['supplied_texts'])

    def test_0853_source_and_capture_keep_their_fields_and_digests_after_import(self):
        fixture = json.loads((ROOT / 'tests/fixtures/research/layout-capture-0.85.3.json').read_text())
        capture, source = fixture['capture'], fixture['source']
        version = 'arxiv:2601.00001v1'
        acquisition.import_response(self.store, 'arxiv', atom([entry()], total=1),
            source_url='https://export.arxiv.org/api/query?id_list=2601.00001v1',
            captured_at='2026-09-08T00:00:00+00:00', expected_revision=self.store.revision,
            request_id='legacy-metadata')
        fixtures = ROOT / 'tests/fixtures/research'
        self.assertEqual(self.artifacts.put((fixtures / 'dense-label-layout.pdf').read_bytes(),
                                           'application/pdf'), capture['original'])
        self.assertEqual(self.artifacts.put((fixtures / 'dense-label-layout-layout.txt').read_bytes(),
                                           'text/plain; charset=utf-8'), capture['text'])
        def retain(transaction):
            transaction.put('source', source['id'], source)
            work = transaction.get('work', version)
            work['fulltexts'].append(capture)
            transaction.put('work', work['id'], work)
        self.store.mutate('fixture.legacy', {}, retain,
            expected_revision=self.store.revision, request_id='legacy')
        before = self.store.snapshot()['records']
        self.import_text()
        records = Store(self.root).snapshot()['records']
        self.assertEqual(records['source'][source['id']], before['source'][source['id']])
        self.assertEqual(records['work'][version], before['work'][version])
        self.assertEqual(digest(records['source'][source['id']]),
                         'efcdc1dca8eee5835a3b20ea9049d83f017376f59244521e43550a85f1ca4988')
        self.assertEqual(digest(records['work'][version]['fulltexts'][0]), fixture['capture_digest'])
        from research_harness.graph import main_captures
        self.assertEqual(main_captures(records, records['work'][version]), [capture])
        self.assertTrue(source_links.complete_original({'source': source, 'capture': capture}))
        self.assertGreater(check_integrity(self.store)['artifacts'], 0)


if __name__ == '__main__':
    unittest.main()
