"""Resumable extraction uses authored tools and a durable injected unit store."""

import copy
import errno
import hashlib
import importlib
import importlib.util
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.fulltext import PdfExtractor
from research_harness.ocr import OcrPdfExtractor


class DiskUnits:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(exist_ok=True)

    def read_committed_units(self, job_id):
        path = self.root / (job_id + '.json')
        return json.loads(path.read_text()) if path.exists() else []

    def commit_unit(self, job_id, unit):
        units = self.read_committed_units(job_id)
        units.append(unit)
        target = self.root / (job_id + '.json')
        temporary = target.with_suffix('.tmp')
        with temporary.open('w') as stream:
            json.dump(units, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)


class ExtractionUnitsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.artifacts = ArtifactStore(self.root)
        self.original = self.artifacts.put(b'%PDF-authored complete original', 'application/pdf')
        self.units = DiskUnits(self.root / 'units')
        self.log = self.root / 'calls'
        self.versions = {'pdfinfo': 'pdfinfo version 1.0', 'pdftotext': 'pdftotext version 1.0'}
        self.create_tools()

    def create_tools(self, *, pages=3, text_code=None, render_code=None):
        prelude = ("import pathlib, sys, time, os\n"
                   "if sys.argv[1:] in (['-v'], ['--version']):\n"
                   "    print(pathlib.Path(sys.argv[0]).name + ' version 1.0'); raise SystemExit(0)\n")
        codes = {
            'pdfinfo': "with open(%r, 'a') as log: log.write('info\\n')\nprint('Pages: %d')\n" % (str(self.log), pages),
            'pdftotext': (
                "assert sys.argv[1:3] == ['-enc', 'UTF-8']\n"
                "page = int(sys.argv[sys.argv.index('-f') + 1])\n"
                "assert sys.argv[sys.argv.index('-l') + 1] == str(page)\n"
                "assert pathlib.Path(sys.argv[-2]).read_bytes().startswith(b'%%PDF-')\n"
                "with open(%r, 'a') as log: log.write(str(page) + '\\n')\n" % str(self.log)
                + (text_code or "sys.stdout.write('page ' + str(page) + '\\n\\f')\n")),
            'pdftoppm': render_code or "sys.stdout.buffer.write(b'P5\\n1 1\\n255\\nX')\n",
            'tesseract': "assert pathlib.Path(sys.argv[1]).read_bytes().startswith(b'P5')\nprint('OCR page')\n",
        }
        for name, code in codes.items():
            path = self.root / name
            path.write_text('#!' + sys.executable + '\n' + prelude + code)
            path.chmod(0o700)

    def build_engine(self, *, extractor=None, units=None):
        self.assertIsNotNone(importlib.util.find_spec('research_harness.extraction_units'),
                             'D6-6 page extraction engine is missing')
        module = importlib.import_module('research_harness.extraction_units')
        extractor = extractor or PdfExtractor(executable=str(self.root / 'pdftotext'),
                                              pdfinfo_executable=str(self.root / 'pdfinfo'))
        return module.ExtractionEngine(self.artifacts, units or DiskUnits(self.root / 'units'),
                                       self.root / 'extraction', extractor)

    def resume(self, engine=None, **changes):
        arguments = dict(original=self.original, work_id='arxiv:1234.5678', version='v2',
                         tool_versions=self.versions, options={'layout': True}, max_pages=100,
                         max_seconds=600)
        arguments.update(changes)
        return (engine or self.build_engine()).resume('job', **arguments)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_201_pages_complete_over_multiple_invocations_with_one_page_count(self):
        self.create_tools(pages=201)
        for count in (70, 140):
            result = self.resume(max_pages=70)
            self.assertEqual(result['completed_pages'], count)
            self.assertEqual(result['availability'], 'pending')
            self.assertIsNone(result['text'])
        result = self.resume(max_pages=70)
        expected = ''.join('page %d\n\f' % page for page in range(1, 202)).encode()
        self.assertEqual(result['availability'], 'available')
        self.assertEqual(result['extraction']['protocol'], 'pdf-pages-v1')
        self.assertEqual(result['text']['sha256'], hashlib.sha256(expected).hexdigest())
        self.assertEqual(self.artifacts.read(result['text']), expected)
        self.assertEqual(result['original'], self.original)
        self.assertEqual(self.calls(), ['info'] + [str(page) for page in range(1, 202)])
        self.assertEqual(result['extraction']['page_count'], 201)
        self.assertTrue(result['visual_inspection_required'])

    def test_aggregate_text_can_exceed_the_legacy_bound(self):
        self.create_tools(text_code="sys.stdout.write('x' * 40)\n")
        extractor = PdfExtractor(executable=str(self.root / 'pdftotext'),
                                 pdfinfo_executable=str(self.root / 'pdfinfo'), max_text_bytes=64)
        result = self.resume(self.build_engine(extractor=extractor))
        self.assertEqual(result['availability'], 'available')
        self.assertEqual(self.artifacts.read(result['text']), (b'x' * 40 + b'\f') * 3)
        self.assertGreater(result['text']['size'], extractor.max_text_bytes)

    def test_page_allowance_is_independent_of_the_supervisor_log_limit(self):
        self.create_tools(pages=1, text_code="sys.stdout.write('x' * 100)\n")
        extractor = PdfExtractor(executable=str(self.root / 'pdftotext'),
                                 pdfinfo_executable=str(self.root / 'pdfinfo'), max_text_bytes=128)
        with patch('research_harness.probe_worker.STREAM_LIMIT', 64):
            result = self.resume(self.build_engine(extractor=extractor))
        self.assertEqual(result['availability'], 'available')
        self.assertEqual(self.artifacts.read(result['text']), b'x' * 100 + b'\f')

    def test_interruption_after_commit_never_reextracts_completed_pages(self):
        class InterruptedUnits(DiskUnits):
            def commit_unit(self, job_id, unit):
                super().commit_unit(job_id, unit)
                if unit['kind'] == 'page' and unit['page'] == 2:
                    raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.resume(self.build_engine(units=InterruptedUnits(self.units.root)))
        result = self.resume()
        self.assertEqual(result['availability'], 'available')
        self.assertEqual(self.calls(), ['info', '1', '2', '3'])

    def test_empty_pages_are_committed_and_a_failed_or_missing_page_blocks_completion(self):
        self.create_tools(text_code="if page == 3: raise SystemExit(1)\nif page == 2: sys.stdout.write('body')\n")
        result = self.resume(max_pages=1)
        self.assertIsNone(result['text'])
        pages = [u for u in self.units.read_committed_units('job') if u['kind'] == 'page']
        self.assertEqual(self.artifacts.read(pages[0]['text']), b'')
        result = self.resume()
        self.assertEqual(result['completed_pages'], 2)
        self.assertEqual(result['availability'], 'pending')
        self.assertEqual(result['pending']['page'], 3)
        self.assertIsNone(result['text'])
        self.create_tools(text_code="if page == 2: sys.stdout.write('body')\n")
        result = self.resume()
        self.assertEqual(self.artifacts.read(result['text']), b'\fbody\f\f')
        self.assertEqual(self.calls(), ['info', '1', '2', '3', '3'])

    def test_wholly_empty_document_stays_empty_text(self):
        self.create_tools(text_code="pass\n")
        result = self.resume()
        self.assertEqual(result['availability'], 'pending')
        self.assertEqual(result['extraction_status'], 'empty_text')
        self.assertIsNone(result['text'])
        self.assertEqual(result['completed_pages'], 3)

    def test_units_from_other_original_version_tools_or_options_are_refused(self):
        self.resume(max_pages=1)
        saved = self.units.read_committed_units('job')
        for field, value in (
            ('original', self.artifacts.put(b'%PDF-another original', 'application/pdf')),
            ('version', 'v3'), ('tool_versions', dict(self.versions, pdftotext='other')),
            ('options', {'layout': False}), ('options', {'layout': 1}),
        ):
            units = copy.deepcopy(saved)
            units[1]['binding'][field] = value
            (self.units.root / 'job.json').write_text(json.dumps(units))
            with self.subTest(field=field):
                with self.assertRaises(ResearchError) as error:
                    self.resume()
                self.assertEqual(error.exception.code, 'record_conflict')
        self.assertEqual(self.calls(), ['info', '1'])

    def test_unversioned_doi_identity_keeps_the_existing_null_version_shape(self):
        self.create_tools(pages=1)
        with self.assertRaises(ResearchError) as error:
            self.resume(version=None)
        self.assertEqual(error.exception.code, 'invalid_input')
        result = self.resume(work_id='doi:10.1234/authored', version=None)
        self.assertEqual(result['availability'], 'available')
        self.assertIsNone(result['version'])
        with self.assertRaises(ResearchError) as error:
            self.resume(work_id='doi:10.1234/authored', version='2')
        self.assertEqual(error.exception.code, 'record_conflict')
        self.assertEqual(self.calls(), ['info', '1'])

    def test_pinned_options_preserve_json_types(self):
        with self.assertRaises(ResearchError) as error:
            self.resume(options={'layout': 1})
        self.assertEqual(error.exception.code, 'record_conflict')
        self.assertEqual(self.calls(), [])

    def test_changed_actual_tool_version_is_refused_before_extraction(self):
        self.resume(max_pages=1)
        path = self.root / 'pdftotext'
        path.write_text(path.read_text().replace('version 1.0', 'version 2.0'))
        with self.assertRaises(ResearchError) as error:
            self.resume()
        self.assertEqual(error.exception.code, 'record_conflict')
        self.assertEqual(self.calls(), ['info', '1'])

    def test_timeout_partial_output_and_page_output_limit_do_not_commit_a_page(self):
        for text_code, settings, measure, limit in (
            ("sys.stdout.write('partial'); sys.stdout.flush(); time.sleep(3)\n", {'timeout': 0.5}, 'elapsed_seconds', 0.5),
            ("sys.stdout.write('x' * 100)\n", {'max_text_bytes': 20}, 'output_bytes', 20),
        ):
            with self.subTest(measure=measure):
                self.create_tools(text_code=text_code)
                extractor = PdfExtractor(executable=str(self.root / 'pdftotext'),
                                         pdfinfo_executable=str(self.root / 'pdfinfo'), **settings)
                result = self.resume(self.build_engine(extractor=extractor))
                self.assertEqual(result['pending']['code'], 'fulltext_unit_limit')
                self.assertEqual(result['pending']['page'], 1)
                self.assertGreaterEqual(result['pending']['usage'][measure], limit)
                self.assertEqual(result['pending']['limit'][measure], limit)
                self.assertTrue(result['pending']['recovery_action'])
                self.assertFalse(result['pending']['retryable_with_same_configuration'])
                self.assertEqual(result['completed_pages'], 0)
                self.assertIsNone(result['text'])

    def test_page_deadline_includes_supervisor_launch_delay(self):
        from research_harness.probe_worker import run_probe_process
        self.create_tools(pages=1)
        extractor = PdfExtractor(executable=str(self.root / 'pdftotext'),
                                 pdfinfo_executable=str(self.root / 'pdfinfo'), timeout=10)

        def delay_launch(root, config, *arguments):
            if '-f' in config['tool_argv']:
                time.sleep(11)
            return run_probe_process(root, config, *arguments)

        with patch('research_harness.probe_worker.run_probe_process', side_effect=delay_launch):
            result = self.resume(self.build_engine(extractor=extractor))
        self.assertEqual(result['availability'], 'pending')
        self.assertEqual(result['pending']['code'], 'fulltext_unit_limit')
        self.assertEqual(result['pending']['page'], 1)
        self.assertGreaterEqual(result['pending']['usage']['elapsed_seconds'], 10)
        self.assertEqual(result['pending']['limit']['elapsed_seconds'], 10)
        self.assertEqual(result['completed_pages'], 0)
        self.assertIsNone(result['text'])

    def test_ocr_timeout_before_recognition_reports_the_page_time_limit(self):
        self.create_tools(pages=1)
        versions = {name: name + ' version 1.0' for name in ('pdfinfo', 'pdftoppm', 'tesseract')}
        extractor = OcrPdfExtractor(executables={name: str(self.root / name) for name in versions}, timeout=10)
        write_bytes = Path.write_bytes

        def delay_image(path, data):
            if path.name == 'page.pgm':
                time.sleep(11)
            return write_bytes(path, data)

        with patch.object(Path, 'write_bytes', autospec=True, side_effect=delay_image):
            result = self.resume(self.build_engine(extractor=extractor), tool_versions=versions,
                                 options=extractor.get_page_options())
        self.assertEqual(result['pending']['code'], 'fulltext_unit_limit')
        self.assertEqual(result['pending']['page'], 1)
        self.assertGreaterEqual(result['pending']['usage']['elapsed_seconds'], 10)
        self.assertEqual(result['pending']['limit']['elapsed_seconds'], 10)
        self.assertIsNone(result['text'])

    def test_ocr_uses_a_batch_page_allowance_and_retains_image_limit_diagnostics(self):
        versions = {name: name + ' version 1.0' for name in ('pdfinfo', 'pdftoppm', 'tesseract')}
        extractor = OcrPdfExtractor(executables={name: str(self.root / name) for name in versions},
                                    max_pages=1, max_image_bytes=64)
        engine = self.build_engine(extractor=extractor)
        result = self.resume(engine, tool_versions=versions, options=extractor.get_page_options(), max_pages=None)
        self.assertEqual(result['completed_pages'], 1)
        result = self.resume(self.build_engine(extractor=extractor), tool_versions=versions,
                             options=extractor.get_page_options(), max_pages=None)
        self.assertEqual(result['completed_pages'], 2)
        self.create_tools(render_code="sys.stdout.write('P5' + 'x' * 100)\n")
        result = self.resume(self.build_engine(extractor=extractor), tool_versions=versions,
                             options=extractor.get_page_options(), max_pages=None)
        self.assertEqual(result['pending']['page'], 3)
        self.assertGreater(result['pending']['usage']['image_bytes'], 64)
        self.assertEqual(result['pending']['limit']['image_bytes'], 64)
        self.assertIsNone(result['text'])

    def test_commit_retry_reuses_the_captured_page(self):
        class BusyUnits(DiskUnits):
            def __init__(self, root):
                super().__init__(root)
                self.busy = True
            def commit_unit(self, job_id, unit):
                if unit['kind'] == 'page' and self.busy:
                    self.busy = False
                    raise ResearchError('store_busy', 'authored commit contention')
                super().commit_unit(job_id, unit)
        result = self.resume(self.build_engine(units=BusyUnits(self.units.root)))
        self.assertEqual(result['availability'], 'available')
        self.assertEqual(self.calls(), ['info', '1', '2', '3'])

    def test_incremental_measures_match_the_assembled_page_text(self):
        self.create_tools(pages=1, text_code="sys.stdout.write('ab cd\\nxy\\n')\n")
        result = self.resume()
        self.assertEqual(result['extraction']['max_line_length'], 5)
        self.assertEqual(result['extraction']['whitespace_fraction'], 0.4)
        self.assertEqual(result['extraction']['text_bytes'], 10)

    def test_page_count_mismatch_and_failed_committed_pages_are_refused(self):
        self.resume(max_pages=1)
        saved = self.units.read_committed_units('job')
        for field, value in (('page_count', 4), ('status', 'extraction_failed')):
            units = copy.deepcopy(saved)
            units[1][field] = value
            (self.units.root / 'job.json').write_text(json.dumps(units))
            with self.subTest(field=field):
                with self.assertRaises(ResearchError) as error:
                    self.resume()
                self.assertEqual(error.exception.code, 'record_conflict')

    def test_exhausted_commit_retry_keeps_tool_output_for_the_next_invocation(self):
        class BlockedUnits(DiskUnits):
            def commit_unit(self, job_id, unit):
                if unit['kind'] == 'page':
                    raise ResearchError('store_busy', 'authored unavailable store')
                super().commit_unit(job_id, unit)
        with patch('research_harness.extraction_units.retry_store_write', side_effect=lambda write: write()):
            result = self.resume(self.build_engine(units=BlockedUnits(self.units.root)))
        self.assertEqual(result['completed_pages'], 0)
        self.assertEqual(result['availability'], 'pending')
        result = self.resume()
        self.assertEqual(result['availability'], 'available')
        self.assertEqual(self.calls(), ['info', '1', '2', '3'])

    def test_disk_full_during_assembly_keeps_committed_pages(self):
        engine = self.build_engine()
        for failure in (OSError(errno.ENOSPC, 'authored full disk'),
                        ResearchError('storage_io', 'authored full disk', {'errno': errno.ENOSPC})):
            with self.subTest(failure=type(failure).__name__):
                with patch.object(self.artifacts, 'put_file', side_effect=failure):
                    result = self.resume(engine)
                self.assertEqual(result['availability'], 'pending')
                self.assertEqual(result['pending']['code'], 'fulltext_storage_limit')
                self.assertEqual(result['completed_pages'], 3)
                self.assertIsNone(result['text'])
        self.assertEqual(self.resume()['availability'], 'available')
        self.assertEqual(self.calls(), ['info', '1', '2', '3'])

    def test_corrupt_retained_original_is_refused_before_the_next_page(self):
        self.resume(max_pages=1)
        path = self.root / self.original['path']
        path.chmod(0o600)
        path.write_bytes(b'changed')
        with self.assertRaises(ResearchError) as error:
            self.resume()
        self.assertEqual(error.exception.code, 'artifact_corrupt')
        self.assertEqual(self.calls(), ['info', '1'])

    def test_invocation_deadline_remains_resumable_with_the_same_page_allowances(self):
        result = self.resume(max_seconds=0.01)
        self.assertEqual(result['availability'], 'pending')
        self.assertEqual(result['pending']['code'], 'fulltext_job_pending')
        self.assertEqual(result['pending']['recovery_action'], 'resume_extraction')
        self.assertIsNone(result['text'])
        self.assertEqual(self.resume()['availability'], 'available')

    def test_supervised_worker_recovers_after_parent_death_without_overlap(self):
        self.recover_after_parent_death()

    def test_ocr_worker_recovery_reclaims_the_interrupted_page_image(self):
        self.recover_after_parent_death(ocr=True)

    def recover_after_parent_death(self, *, ocr=False):
        body = ("pathlib.Path(%r).write_text(str(os.getpid()))\n" % str(self.root / 'tool.pid') +
                "time.sleep(1.5)\nsys.stdout.write('surviving page')\n")
        self.create_tools(pages=1, text_code=body)
        expected_calls = ['info', '1']
        if ocr:
            self.create_tools(pages=1, render_code=(
                "with open(%r, 'a') as log: log.write('1\\n')\n" % str(self.log) +
                "sys.stdout.buffer.write(b'P5\\n1 1\\n255\\nX')\n"))
            path = self.root / 'tesseract'
            path.write_text(path.read_text().replace("print('OCR page')", (
                "pathlib.Path(%r).write_text(sys.argv[1])\n" % str(self.root / 'image-path') +
                "with open(%r, 'a') as log: log.write('ocr\\n')\n" % str(self.log) + body)))
            versions = {name: name + ' version 1.0' for name in ('pdfinfo', 'pdftoppm', 'tesseract')}
            executables = {name: str(self.root / name) for name in versions}
            extractor = OcrPdfExtractor(executables=executables)
            constructor = 'OcrPdfExtractor(executables=%r)' % executables
            expected_calls.append('ocr')
        else:
            versions = self.versions
            extractor = PdfExtractor(executable=str(self.root / 'pdftotext'),
                                     pdfinfo_executable=str(self.root / 'pdfinfo'))
            constructor = "PdfExtractor(executable=str(root / 'pdftotext'), pdfinfo_executable=str(root / 'pdfinfo'))"
        options = extractor.get_page_options()
        self.build_engine(extractor=extractor)
        script = (
            "from pathlib import Path\n"
            "from test_research_extraction_units import DiskUnits\n"
            "from research_harness.artifacts import ArtifactStore\n"
            "from research_harness.fulltext import PdfExtractor\n"
            "from research_harness.ocr import OcrPdfExtractor\n"
            "from research_harness.extraction_units import ExtractionEngine\n"
            "root = Path(%r)\n" % str(self.root) +
            "engine = ExtractionEngine(ArtifactStore(root), DiskUnits(root / 'units'), root / 'extraction', " + constructor + ")\n" +
            "engine.resume('job', original=%r, work_id='arxiv:1234.5678', version='v2', " % self.original +
            "tool_versions=%r, options=%r, max_pages=10, max_seconds=120)\n" % (versions, options))
        parent = subprocess.Popen([sys.executable, '-c', script], env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        try:
            deadline = time.monotonic() + 15
            while not (self.root / 'tool.pid').exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue((self.root / 'tool.pid').exists())
            image = Path((self.root / 'image-path').read_text()) if ocr else None
            if image is not None:
                self.addCleanup(shutil.rmtree, image.parent, True)
            parent.kill()
            parent.wait(timeout=5)
            result = self.resume(self.build_engine(extractor=extractor), tool_versions=versions, options=options)
            self.assertEqual(result['availability'], 'pending')
            self.assertEqual(self.calls(), expected_calls)
            deadline = time.monotonic() + 15
            while result['availability'] == 'pending' and time.monotonic() < deadline:
                time.sleep(0.05)
                result = self.resume(self.build_engine(extractor=extractor), tool_versions=versions, options=options)
            self.assertEqual(result['availability'], 'available')
            self.assertEqual(self.artifacts.read(result['text']), b'surviving page\f')
            self.assertEqual(self.calls(), expected_calls)
            self.assertEqual(list((self.root / 'extraction').rglob('.artifact-*')), [])
            if image is not None:
                self.assertFalse(image.exists(), 'Recovered OCR image must not remain outside job cleanup')
        finally:
            if parent.poll() is None:
                parent.kill()
            parent.wait()


if __name__ == '__main__':
    unittest.main()
