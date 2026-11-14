"""Default layout selection uses measured padding, preserving explicit and stored captures."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from research_fixtures import client
from research_harness import fulltext
from research_harness.acquisition import acquire_fulltext
from research_harness.evidence import digest


FIXTURES = Path(__file__).parent / 'fixtures/research'


class PdfToolFixtures:
    def install_pdf_tool(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        executable = Path(temporary.name) / 'pdftotext'
        executable.write_text('#!' + sys.executable + '\n' +
            'import pathlib, sys\n'
            'if sys.argv[1:] == ["-v"]:\n'
            '    sys.stderr.write("pdftotext version 26.02.0\\n"); raise SystemExit(0)\n'
            'root = pathlib.Path(' + repr(str(FIXTURES)) + ')\n'
            'assert sys.argv[1:3] == ["-enc", "UTF-8"] and sys.argv[-1] == "-"\n'
            'data = pathlib.Path(sys.argv[-2]).read_bytes()\n'
            'name = next(name for name in ("ordinary-layout", "dense-label-layout")\n'
            '            if (root / (name + ".pdf")).read_bytes() == data)\n'
            'mode = "layout" if "-layout" in sys.argv else "plain"\n'
            'sys.stdout.buffer.write((root / (name + "-" + mode + ".txt")).read_bytes())\n')
        executable.chmod(0o700)
        real = fulltext.PdfExtractor
        patched = patch.object(fulltext, 'PdfExtractor', side_effect=lambda **options: real(executable=str(executable), **options))
        patched.start()
        self.addCleanup(patched.stop)

    def extract_fixture(self, name, **options):
        return fulltext.extract((FIXTURES / (name + '.pdf')).read_bytes(), 'application/pdf', **options)


class CompactFulltextTests(PdfToolFixtures, unittest.TestCase):
    def setUp(self):
        self.install_pdf_tool()

    def test_dense_figure_labels_keep_the_complete_plain_extraction_and_both_measurements(self):
        result = self.extract_fixture('dense-label-layout')
        self.assertEqual(result['text'].encode(), (FIXTURES / 'dense-label-layout-plain.txt').read_bytes())
        self.assertEqual(result['options'], {'layout': False})
        selection = result['layout_selection']
        self.assertEqual(selection['protocol'], fulltext.LAYOUT_SELECTION_PROTOCOL)
        self.assertEqual(selection['kept_mode'], 'plain')
        for mode in ('layout', 'plain'):
            data = (FIXTURES / ('dense-label-layout-' + mode + '.txt')).read_bytes()
            self.assertEqual(selection['measurements'][mode], fulltext.extraction_measures(
                data.decode(), (FIXTURES / 'dense-label-layout.pdf').stat().st_size))

    def test_ordinary_fixture_keeps_layout_bytes_without_running_plain_extraction(self):
        result = self.extract_fixture('ordinary-layout')
        self.assertEqual(result['text'].encode(), (FIXTURES / 'ordinary-layout-layout.txt').read_bytes())
        self.assertEqual(result['options'], {'layout': True})
        self.assertEqual(result['layout_selection']['kept_mode'], 'layout')
        self.assertEqual(set(result['layout_selection']['measurements']), {'layout'})

    def test_compact_retry_preserves_generic_binary_pdf_detection_provenance(self):
        result = fulltext.extract((FIXTURES / 'dense-label-layout.pdf').read_bytes(), 'application/octet-stream')
        self.assertEqual(result['layout_selection']['kept_mode'], 'plain')
        self.assertEqual(result['media_type'], 'application/pdf')
        self.assertEqual(result['format_detection'], 'pdf_signature_from_generic_binary')

    def test_explicit_layout_flags_bypass_automatic_selection(self):
        self.assertEqual(self.extract_fixture('dense-label-layout')['layout_selection']['kept_mode'], 'plain')
        for layout, mode in ((True, 'layout'), (False, 'plain')):
            with self.subTest(layout=layout):
                result = self.extract_fixture('dense-label-layout', layout=layout)
                self.assertEqual(result['options'], {'layout': layout})
                self.assertNotIn('layout_selection', result)
                self.assertEqual(result['text'].encode(), (FIXTURES / ('dense-label-layout-' + mode + '.txt')).read_bytes())

    def test_either_measurement_triggers_but_threshold_equality_keeps_layout(self):
        pdf = (FIXTURES / 'ordinary-layout.pdf').read_bytes()
        for whitespace, expansion, retry in ((0.91, 1.0, True), (0.3, 2.001, True), (0.9, 2.0, False)):
            with self.subTest(whitespace=whitespace, expansion=expansion):
                layout = {'status': 'extracted', 'text': 'The layout text.', 'options': {'layout': True}}
                plain = {'status': 'extracted', 'text': 'The plain text.', 'options': {'layout': False}}
                with patch.object(fulltext, 'PdfExtractor', side_effect=[lambda data: layout, lambda data: plain]) as tool, \
                        patch.object(fulltext, 'extraction_measures', return_value={
                            'whitespace_fraction': whitespace, 'expansion_ratio': expansion}):
                    result = fulltext.extract(pdf, 'application/pdf')
                self.assertEqual(tool.call_count, 2 if retry else 1)
                self.assertEqual(result['layout_selection']['kept_mode'], 'plain' if retry else 'layout')

    def test_failed_plain_extraction_stays_pending_instead_of_returning_inflated_text(self):
        layout = {'status': 'extracted', 'text': 'x' + ' ' * 1000}
        for status in ('empty_text', 'extraction_timeout', 'extraction_too_large'):
            with self.subTest(status=status), patch.object(fulltext, 'PdfExtractor', side_effect=[
                    lambda data: layout, lambda data: {'status': status, 'text': None}]):
                result = self.extract_fixture('ordinary-layout')
                self.assertEqual(result['status'], status)
                self.assertIsNone(result['text'])
                self.assertIsNone(result['layout_selection']['kept_mode'])
                self.assertEqual(result['layout_selection']['plain_status'], status)

    def test_measured_sample_separates_thirty_ordinary_articles_from_three_inflated_pdfs(self):
        sample = json.loads((FIXTURES / 'pdf-layout-measurements.json').read_text())
        self.assertEqual(sample['thresholds'], {'whitespace_fraction': fulltext.LAYOUT_WHITESPACE_THRESHOLD,
                                               'expansion_ratio': fulltext.LAYOUT_EXPANSION_THRESHOLD})
        ordinary = [row for row in sample['rows'] if row['group'] == 'ordinary']
        self.assertEqual(len({row['original']['sha256'] for row in ordinary}), 30)
        self.assertTrue(all(row['stored_layout_matches'] for row in ordinary))
        for row in sample['rows']:
            measures = row['measurements']['layout']
            inflated = any(measures[key] > threshold for key, threshold in sample['thresholds'].items())
            self.assertEqual(inflated, row['group'] == 'incident' and row['version_id'] != 'arxiv:2401.01860v3')
            if inflated:
                self.assertLess(row['measurements']['plain']['characters'], 300000)
        for key, threshold in sample['thresholds'].items():
            self.assertGreater(sample['margins'][key]['ordinary_to_threshold'], 0)
            self.assertGreater(sample['margins'][key]['threshold_to_inflated'], 0)


class CompactCaptureTests(PdfToolFixtures, LiteratureCase):
    def test_stored_0853_capture_keeps_its_frozen_digest_text_and_default_projection(self):
        self.install_pdf_tool()
        version = self.metadata()
        legacy = json.loads((FIXTURES / 'layout-capture-0.85.3.json').read_text())
        capture = legacy['capture']
        self.assertEqual(legacy['capture_digest'], '56bef754f3ad8576976e1e45f67265e84c27aa13ae35b6872b9f80b65cb36454')
        pdf = (FIXTURES / 'dense-label-layout.pdf').read_bytes()
        self.assertEqual(self.artifacts.put(pdf, 'application/pdf'), capture['original'])
        self.assertEqual(self.artifacts.put((FIXTURES / 'dense-label-layout-layout.txt').read_bytes(),
                                           'text/plain; charset=utf-8'), capture['text'])
        def install(transaction):
            work = transaction.get('work', version)
            work['fulltexts'].append(capture)
            transaction.put('source', legacy['source']['id'], legacy['source'])
            transaction.put('work', version, work)
        self.store.mutate('fixture.0853_capture', {}, install, expected_revision=self.store.revision, request_id='install')
        from research_harness.graph import main_captures
        records = self.store.snapshot()['records']
        self.assertEqual(main_captures(records, records['work'][version]), [capture])
        http, _, _ = client([(200, {'Content-Type': 'application/pdf'}, pdf)], max_retries=0)
        new = acquire_fulltext(self.store, version, 'https://arxiv.org/pdf/' + version[6:], http=http,
                               expected_revision=self.store.revision, request_id='automatic')['capture']
        self.assertEqual(new['extraction']['layout_selection']['kept_mode'], 'plain')
        records = self.store.snapshot()['records']
        self.assertEqual(main_captures(records, records['work'][version]), [capture])
        self.assertEqual(digest(records['work'][version]['fulltexts'][0]), legacy['capture_digest'])
        self.assertEqual(self.artifacts.read(capture['text']), (FIXTURES / 'dense-label-layout-layout.txt').read_bytes())

    def test_default_capture_records_selection_and_replay_keeps_legacy_layout_capture_digest_and_bytes(self):
        self.install_pdf_tool()
        version = self.metadata()
        pdf = (FIXTURES / 'dense-label-layout.pdf').read_bytes()
        def acquire(request, options=None):
            http, _, _ = client([(200, {'Content-Type': 'application/pdf'}, pdf)], max_retries=0)
            return acquire_fulltext(self.store, version, 'https://arxiv.org/pdf/' + version[6:],
                http=http, extraction_options=options, request_id=request, expected_revision=self.store.revision)
        legacy = acquire('explicit-layout', {'layout': True})
        old = legacy['capture']
        old_digest, old_bytes = digest(old), self.artifacts.read(old['text'])
        automatic = acquire('default')['capture']
        self.assertEqual(automatic['availability'], 'available')
        self.assertEqual(automatic['extraction']['layout_selection']['kept_mode'], 'plain')
        self.assertEqual(automatic['extraction']['options'], {'layout': False})
        self.assertEqual(automatic['text']['sha256'], hashlib.sha256(
            (FIXTURES / 'dense-label-layout-plain.txt').read_bytes()).hexdigest())
        self.assertEqual(acquire('explicit-layout', {'layout': True}), legacy)
        stored = self.store.snapshot()['records']['work'][version]['fulltexts'][0]
        self.assertEqual((digest(stored), self.artifacts.read(stored['text'])), (old_digest, old_bytes))
