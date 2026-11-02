"""Durable, bounded full-text jobs over real store commits and authored tools."""
import copy
import hashlib
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from research_harness import acquisition, cli
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.http import RawResponse
from research_harness.storage import Store
from research_fixtures import atom, entry, client, xml_response, make_http_client
import test_research_extraction_units as extraction_fixtures

VERSION = 'arxiv:2601.00001v1'
URL = 'https://arxiv.org/pdf/2601.00001v1'
BODY = b'%PDF-' + b'x' * 100


class JobCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.log = self.root / 'calls'
        environment = patch.dict(os.environ, HOME=str(self.root), XDG_STATE_HOME=str(self.root / 'state'),
                                 PATH=str(self.root) + os.pathsep + os.environ['PATH'])
        environment.start()
        self.addCleanup(environment.stop)
        extraction_fixtures.ExtractionUnitsTests.create_tools(self, pages=3)
        self.store = Store(self.root, create=True)
        self.artifacts = ArtifactStore(self.root)
        http, _, _ = client([xml_response(atom([entry()], total=1))])
        acquisition.acquire_work(self.store, VERSION, request_id='metadata', expected_revision=0, http=http)
        self.sequence = 0

    def load_jobs(self):
        self.assertIsNotNone(importlib.util.find_spec('research_harness.fulltext_jobs'), 'D6-7 durable fulltext jobs missing')
        return importlib.import_module('research_harness.fulltext_jobs')

    def invoke(self, command, payload, **kwargs):
        self.sequence += 1
        return cli.acquisition_command(self.store, command, payload,
            dict(request_id='command-' + str(self.sequence), expected_revision=self.store.revision), **kwargs)

    def start(self, **changes):
        payload = dict(identifier=VERSION, url=URL, allowances={
            'original_bytes': 1000, 'retained_bytes': 100000, 'text_bytes': 10000,
            'network_attempts': 10, 'invocation_seconds': 30, 'pages_per_resume': 1, 'range_bytes': 40})
        payload.update(changes)
        return self.invoke('fulltext-start', payload)['job_id']

    def resume(self, job_id, *, http=None, **kwargs):
        return self.load_jobs().resume_job(self.store, job_id, request_id='resume-' + str(self.store.revision),
            expected_revision=self.store.revision, http=http, **kwargs)

    def retained(self):
        http, wire, _ = client([(200, {'Content-Type': 'application/pdf'}, BODY)])
        result = acquisition.acquire_fulltext(self.store, VERSION, URL, request_id='legacy',
            expected_revision=self.store.revision, http=http, extractor=lambda data: {'status': 'extraction_timeout', 'text': None})
        return result['capture']['source_id']

    def wire(self, *, changed=False):
        calls = []
        def transport(url, headers, *, address, timeout):
            calls.append(dict(headers))
            first, last = map(int, headers['Range'][6:].split('-'))
            last = min(last, len(BODY) - 1)
            return RawResponse(206, {'Content-Type': 'application/pdf', 'Content-Length': str(last - first + 1),
                'ETag': '"changed"' if changed and first else '"original"',
                'Content-Range': 'bytes %d-%d/%d' % (first, last, len(BODY))}, io.BytesIO(BODY[first:last + 1]), address)
        return make_http_client(transport=transport, resolver=lambda host, timeout: ['93.184.216.34'], max_retries=0), calls


    def finalize_large_source(self):
        from research_harness.http import DEFAULT_MAX_RESPONSE_BYTES
        size = DEFAULT_MAX_RESPONSE_BYTES + 1
        requests = []
        class Stream:
            def __init__(self, start, end):
                self.position, self.end = start, end
            def read(self, amount):
                if not 0 < amount <= 1024 * 1024:
                    raise AssertionError('Transfer used an unbounded read')
                end = min(self.end, self.position + amount)
                prefix = b'%PDF-'[self.position:min(end, 5)] if self.position < 5 else b''
                block = prefix + b'x' * (end - self.position - len(prefix))
                self.position = end
                return block
            def close(self):
                pass
        def transport(url, headers, *, address, timeout):
            start, end = map(int, headers['Range'][6:].split('-'))
            end = min(end, size - 1)
            requests.append((start, end))
            return RawResponse(206, {'Content-Type': 'application/pdf', 'Content-Length': str(end-start+1),
                'ETag': '\"large\"', 'Content-Range': 'bytes %d-%d/%d' % (start, end, size)}, Stream(start, end+1), address)
        http = make_http_client(transport=transport, resolver=lambda host, timeout: ['93.184.216.34'])
        # The authored extractor checks a bounded signature too.
        tool = self.root / 'pdftotext'
        tool.write_text(tool.read_text().replace("pathlib.Path(sys.argv[-2]).read_bytes().startswith(b'%PDF-')",
                                               "pathlib.Path(sys.argv[-2]).open('rb').read(5) == b'%PDF-'"))
        payload = dict(identifier=VERSION, url=URL, allowances={'original_bytes': size, 'retained_bytes': size * 10,
                       'range_bytes': 64 * 1024 * 1024, 'pages_per_resume': 5})
        path = self.root / 'command.json'
        def execute(command, value, request_id):
            path.write_text(json.dumps(value))
            args = cli.build_parser().parse_args([command, '--workspace', str(self.root), '--file', str(path),
                '--request-id', request_id, '--expected-revision', str(self.store.revision)])
            return cli.run(args)
        job = execute('fulltext-start', payload, 'large-start')['job_id']
        with patch('research_harness.fulltext_jobs.HttpClient', return_value=http):
            result = execute('fulltext-resume', {'job_id': job}, 'large-resume')
        self.assertEqual(result['status'], 'complete')
        expected = hashlib.sha256()
        expected.update(b'%PDF-')
        remaining = size - 5
        while remaining:
            count = min(remaining, 1024 * 1024)
            expected.update(b'x' * count)
            remaining -= count
        capture = self.store.snapshot()['records']['work'][VERSION]['fulltexts'][-1]
        self.assertEqual(capture['original']['sha256'], expected.hexdigest())
        self.assertEqual(len(requests), 3)

class FulltextJobTests(JobCase):
    def test_cli_start_resume_status_finalize_committed_units(self):
        self.load_jobs()
        job = self.start()
        http, calls = self.wire()
        with patch('research_harness.fulltext_jobs.HttpClient', return_value=http):
            for index in range(3):
                result = self.invoke('fulltext-resume', {'job_id': job, 'max_requests': 1})
        self.assertEqual(len(calls), 3)
        self.assertEqual(result['completed_pages'], 1)
        with patch('research_harness.fulltext_jobs.HttpClient', side_effect=AssertionError('No refetch')):
            for index in range(2):
                result = self.invoke('fulltext-resume', {'job_id': job})
        self.assertEqual(result['status'], 'complete')
        status_args = cli.build_parser().parse_args(['fulltext-status', '--workspace', str(self.root), '--job-id', job, '--limit', '2'])
        status = cli.run(status_args)
        self.assertLessEqual(len(status['units']), 2)
        self.assertIsNotNone(status['next_cursor'])
        capture = self.store.snapshot()['records']['work'][VERSION]['fulltexts'][-1]
        self.assertEqual(capture['original']['sha256'], hashlib.sha256(BODY).hexdigest())
        self.assertEqual(self.artifacts.read(capture['text']), b'page 1\n\fpage 2\n\fpage 3\n\f')
        self.assertTrue(capture['visual_inspection_required'])
        self.assertNotIn('reading', self.store.snapshot()['records'])

    def test_legacy_original_resumes_with_zero_http_and_appends(self):
        source = self.retained()
        self.load_jobs()
        job = self.start(url=None, source_id=source)
        with patch('research_harness.fulltext_jobs.HttpClient', side_effect=AssertionError('Retained original needs no HTTP')):
            for _ in range(3):
                result = self.resume(job)
        self.assertEqual(result['status'], 'complete')
        captures = self.store.snapshot()['records']['work'][VERSION]['fulltexts']
        self.assertEqual(len(captures), 2)
        self.assertEqual(captures[0]['availability'], 'pending')
        self.assertEqual(captures[-1]['original'], captures[0]['original'])

    def test_unfinished_job_survives_process_exit_and_unrelated_mutation(self):
        self.load_jobs()
        job = self.start(source_id=self.retained(), url=None)
        self.resume(job)
        self.store.mutate('fixture.unrelated', {}, lambda tx: tx.put('fixture', 'other', {'value': 1}),
                          request_id='unrelated', expected_revision=self.store.revision)
        script = "from test_research_fulltext_jobs import resume_worker; resume_worker(%r, %r)" % (str(self.root), job)
        result = subprocess.run([sys.executable, '-c', script], env=dict(os.environ, PYTHONPATH='tests:.'),
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['completed_pages'], 2)
        self.assertEqual(self.resume(job)['status'], 'complete')
        self.assertEqual(self.log.read_text().splitlines(), ['info', '1', '2', '3'])

    def test_allowance_raise_records_reason_and_preserves_expenditure(self):
        self.load_jobs()
        job = self.start()
        http, calls = self.wire()
        self.resume(job, http=http, max_requests=1)
        before = self.load_jobs().read_job_status(self.store, job)
        result = self.resume(job, http=http, max_requests=1, allowances={'network_attempts': 20}, reason='More ranges')
        after = self.load_jobs().read_job_status(self.store, job)
        self.assertEqual(after['attempts_used'], before['attempts_used'] + 1)
        self.assertEqual(after['allowances']['network_attempts'], 20)
        with self.assertRaises(ResearchError):
            self.resume(job, allowances={'network_attempts': 30})

    def test_changed_transfer_and_exhausted_storage_publish_no_capture(self):
        self.load_jobs()
        job = self.start()
        http, calls = self.wire(changed=True)
        self.resume(job, http=http, max_requests=1)
        result = self.resume(job, http=http, max_requests=1)
        self.assertEqual(result['pending'][0]['code'], 'fulltext_transfer_changed')
        self.assertEqual(self.store.snapshot()['records']['work'][VERSION]['fulltexts'], [])
        job2 = self.start(allowances={'original_bytes': 1000, 'retained_bytes': 30})
        result = self.resume(job2, http=self.wire()[0])
        self.assertEqual(result['pending'][0]['code'], 'fulltext_storage_limit')
        self.assertEqual(self.store.snapshot()['records']['work'][VERSION]['fulltexts'], [])

    def test_assembly_provenance_and_native_source_validation(self):
        from research_harness.source_links import captured_source, complete_original
        from research_harness.review_source_complete import _native_alternative
        from research_harness.bar_sources import descriptor
        self.load_jobs()
        job = self.start(allowances={'pages_per_resume': 5})
        result = self.resume(job, http=self.wire()[0])
        self.assertEqual(result['status'], 'complete')
        records = self.store.snapshot()['records']
        capture = records['work'][VERSION]['fulltexts'][-1]
        source = captured_source(records, self.artifacts, capture['source_id'])
        self.assertTrue(complete_original({'source': source, 'capture': capture}))
        row = dict(source_id=source['id'], version_id=VERSION, original_descriptor=descriptor(capture['original']),
                   extraction_descriptor=descriptor(capture['text']), main_representation=True)
        _native_alternative(records, self.artifacts, VERSION, row)
        forged = copy.deepcopy(records)
        assembly = forged['source_assembly'][source['assembly_id']]
        assembly['total'] += 1
        with self.assertRaises(ResearchError):
            captured_source(forged, self.artifacts, source['id'])
        self.assertNotIn('reading', records)

    def test_complete_assembly_requires_supplied_session_bytes_and_incomplete_is_refused(self):
        from research_harness import bar_sources, review_transport
        from research_harness.review_delivered_evidence import project_delivery_view
        from research_harness.review_supplied_evidence import is_received
        self.load_jobs()
        job = self.start(allowances={'pages_per_resume': 5})
        self.assertEqual(self.resume(job, http=self.wire()[0])['status'], 'complete')
        records = self.store.snapshot()['records']
        capture = records['work'][VERSION]['fulltexts'][-1]
        request = dict(id='complete', kind='source', version_id=VERSION, depth='fulltext')
        with review_transport.text_only_delivery(True):
            delivery = bar_sources._source(records, self.artifacts, request, {})
        self.assertEqual(delivery['status'], 'delivered')
        empty = project_delivery_view(records, self.artifacts, [{'source_deliveries': [delivery]}])
        self.assertFalse(is_received(records, capture['text'], empty['artifacts'], [], artifacts=self.artifacts))
        packet = {'source_deliveries': [delivery], 'evidence': [{'artifact': capture['text'], 'encoding': 'utf-8',
                  'content': self.artifacts.read(capture['text']).decode()}]}
        actual = project_delivery_view(records, self.artifacts, [packet])
        self.assertTrue(is_received(records, capture['text'], actual['artifacts'], [], artifacts=self.artifacts))
        forged = copy.deepcopy(records)
        source = forged['source'][capture['source_id']]
        assembly = forged['source_assembly'][source['assembly_id']]
        assembly['unit_count'] -= 1
        with self.assertRaises(ResearchError):
            bar_sources._source(forged, self.artifacts, request, {})

    def test_retained_capture_recovery_links_the_new_capture(self):
        from research_harness.source_links import validate_link, span_locator
        job = self.start(source_id=self.retained(), url=None, allowances={'pages_per_resume': 5})
        self.assertEqual(self.resume(job)['status'], 'complete')
        records = self.store.snapshot()['records']
        capture = records['work'][VERSION]['fulltexts'][-1]
        content = self.artifacts.read(capture['text']).decode()
        link = dict(version_id=VERSION, source_id=capture['source_id'], artifact=capture['text'],
                    locator=span_locator(content, 0, 6))
        self.assertEqual(validate_link(records, self.artifacts, link)['capture'], capture)


    def test_cli_large_original_streams_above_legacy_bound(self):
        self.finalize_large_source()


    def test_interrupted_network_attempt_is_charged_before_response_commit(self):
        job = self.start()
        def interrupted(*args, **kwargs):
            raise KeyboardInterrupt()
        http = make_http_client(transport=interrupted, resolver=lambda host, timeout: ['93.184.216.34'])
        with self.assertRaises(KeyboardInterrupt):
            self.resume(job, http=http, max_requests=1)
        self.assertEqual(self.load_jobs().read_job_status(self.store, job)['attempts_used'], 1)
        self.assertEqual(self.load_jobs().read_job_status(self.store, job)['transfer_units'], 0)

    def test_retained_original_extraction_needs_no_network_budget(self):
        from research_harness import resources
        source = self.retained()
        def configure(tx):
            tx.put('configuration', 'research', {'profile': 'research'})
            tx.put('resource_budget', 'research:literature', {'limits': {'network_requests': 0}})
        self.store.mutate('fixture.budget', {}, configure, request_id='zero-network', expected_revision=self.store.revision)
        job = self.start(source_id=source, url=None, allowances={'pages_per_resume': 5})
        self.assertEqual(self.resume(job, max_requests=0)['status'], 'complete')
        account = resources.account_report(self.store.snapshot()['records'], 'research')
        self.assertEqual(account['literature']['network_requests']['charged'], 0)

    def test_extraction_storage_limit_bounds_tool_output_before_capture(self):
        source = self.retained()
        extraction_fixtures.ExtractionUnitsTests.create_tools(self, pages=1, text_code="sys.stdout.write('x' * 10000)\n")
        job = self.start(source_id=source, url=None, allowances={'retained_bytes': 1000, 'unit_output_bytes': 20000})
        result = self.resume(job)
        self.assertEqual(result['pending'][0]['code'], 'fulltext_storage_limit')
        outputs = list((self.root / '.exactory/fulltext-workers').rglob('tool-output'))
        self.assertLess(sum(path.stat().st_size for path in outputs), 1000)


    def test_complete_retained_assembly_starts_another_extraction_with_no_http(self):
        job = self.start()
        self.resume(job, http=self.wire()[0])
        status = self.load_jobs().read_job_status(self.store, job)
        other = self.start(source_id=status['source_id'], url=None, extraction_options={'layout': False},
                           allowances={'pages_per_resume': 5})
        with patch('research_harness.fulltext_jobs.HttpClient', side_effect=AssertionError('Retained assembly refetched')):
            self.assertEqual(self.resume(other)['status'], 'complete')

    def test_assembly_refuses_weak_validator_and_encoded_attempt_provenance(self):
        from research_harness.source_links import captured_source
        from research_harness.evidence import digest
        job = self.start(allowances={'pages_per_resume': 5})
        self.assertEqual(self.resume(job, http=self.wire()[0])['status'], 'complete')
        records = self.store.snapshot()['records']
        source = records['source'][self.load_jobs().read_job_status(self.store, job)['source_id']]
        for condition in ('weak', 'encoding'):
            forged = copy.deepcopy(records)
            assembly = forged['source_assembly'][source['assembly_id']]
            if condition == 'weak':
                assembly['etag'] = 'W/"original"'
            for record in forged['fulltext_unit'].values():
                if record['job_id'] == job and record['stage'] == 'transfer':
                    unit = record['unit']
                    if condition == 'weak':
                        unit['etag'] = assembly['etag']
                        unit['attempts'][-1]['headers']['etag'] = assembly['etag']
                    else:
                        unit['attempts'][-1]['headers']['content-encoding'] = 'gzip'
                    unit['id'] = digest([job, {k: v for k, v in unit.items() if k != 'id'}])
            with self.assertRaises(ResearchError):
                captured_source(forged, self.artifacts, source['id'])


    def test_captured_original_and_finalization_retry_storage_io_without_refetch(self):
        for target in ('fulltext.original', 'fulltext.finish'):
            job = self.start(allowances={'pages_per_resume': 5, 'range_bytes': 40})
            http, calls = self.wire()
            mutate = self.store.mutate_scoped
            failures = []
            def refuse(operation, *args, **kwargs):
                if operation == target and not failures:
                    failures.append(operation)
                    raise ResearchError('storage_io', 'Injected publication failure')
                return mutate(operation, *args, **kwargs)
            with patch.object(self.store, 'mutate_scoped', side_effect=refuse):
                self.assertEqual(self.resume(job, http=http)['status'], 'complete')
            self.assertEqual(failures, [target])
            self.assertEqual(len(calls), 3)


    def test_crash_before_page_commit_reuses_output_under_tight_storage(self):
        job = self.start(source_id=self.retained(), url=None, allowances={'retained_bytes': 10000, 'pages_per_resume': 1})
        units = self.load_jobs().JobUnits
        commit = units.commit_unit
        def interrupted(owner, job_id, unit):
            if unit['kind'] == 'page':
                raise SystemExit('Crash before commit')
            return commit(owner, job_id, unit)
        with patch.object(units, 'commit_unit', interrupted):
            with self.assertRaises(SystemExit):
                self.resume(job)
        result = self.resume(job)
        self.assertEqual(result['completed_pages'], 1)
        self.assertEqual(self.log.read_text().splitlines(), ['info', '1'])


    def test_committed_retry_prefix_is_counted_once_on_resume(self):
        job = self.start(allowances={'retained_bytes': 265, 'original_bytes': 1000, 'range_bytes': 40})
        http, calls = self.wire()
        transport = http.transport
        class InterruptedStream(io.BytesIO):
            def read(self, amount=-1):
                if self.tell():
                    raise TimeoutError('Interrupted first attempt')
                return super().read(min(amount, 10))
        def first_fails(*args, **kwargs):
            raw = transport(*args, **kwargs)
            if len(calls) == 1:
                raw.stream = InterruptedStream(BODY[:40])
            return raw
        http.transport = first_fails
        http.max_retries = 1
        self.resume(job, http=http, max_requests=2)
        self.resume(job, http=http, max_requests=2)
        status = self.load_jobs().read_job_status(self.store, job)
        self.assertEqual(status['transferred_bytes'], len(BODY))
        self.assertIsNotNone(status['original'])
        self.assertEqual(status['original']['sha256'], hashlib.sha256(BODY).hexdigest())
        self.assertEqual(status['attempts_used'], 4)

    def test_padded_decimal_range_headers_keep_the_completed_source_valid(self):
        from research_harness.source_links import captured_source
        job = self.start(allowances={'pages_per_resume': 5, 'range_bytes': 40})
        http, calls = self.wire()
        transport = http.transport
        def padded(*args, **kwargs):
            raw = transport(*args, **kwargs)
            raw.headers['Content-Length'] = '000' + raw.headers['Content-Length']
            first, last = map(int, calls[-1]['Range'][6:].split('-'))
            last = min(last, len(BODY)-1)
            raw.headers['Content-Range'] = 'bytes %05d-%05d/%05d' % (first, last, len(BODY))
            return raw
        http.transport = padded
        self.assertEqual(self.resume(job, http=http)['status'], 'complete')
        records = self.store.snapshot()['records']
        capture = records['work'][VERSION]['fulltexts'][-1]
        captured_source(records, self.artifacts, capture['source_id'])


    def test_generic_binary_pdf_keeps_its_visual_page_map(self):
        from research_harness.source_links import validate_link
        job = self.start(allowances={'pages_per_resume': 5})
        http, calls = self.wire()
        transport = http.transport
        def generic(*args, **kwargs):
            raw = transport(*args, **kwargs)
            raw.headers['Content-Type'] = 'application/octet-stream'
            return raw
        http.transport = generic
        self.assertEqual(self.resume(job, http=http)['status'], 'complete')
        records = self.store.snapshot()['records']
        capture = records['work'][VERSION]['fulltexts'][-1]
        link = dict(version_id=VERSION, source_id=capture['source_id'], artifact=capture['original'],
                    locator=dict(kind='pdf', page_index=2, printed_page=None, region=[0,0,1,1]))
        self.assertEqual(validate_link(records, self.artifacts, link)['capture'], capture)
        self.assertTrue(capture['visual_inspection_required'])



def resume_worker(root, job):
    from research_harness.fulltext_jobs import resume_job
    store = Store(Path(root))
    print(json.dumps(resume_job(store, job, request_id='worker-' + str(store.revision), expected_revision=store.revision)))


def transfer_worker(root, job, barrier=None):
    fixture = JobCase()
    fixture.root = Path(root)
    fixture.store = Store(fixture.root)
    fixture.sequence = 0
    http = fixture.wire()[0]
    if barrier is not None:
        import time
        transport = http.transport
        def blocked(*args, **kwargs):
            Path(barrier + '.ready').write_text('ready')
            deadline = time.monotonic() + 20
            while not Path(barrier + '.release').exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError('Metadata writer did not release transfer')
                time.sleep(0.01)
            return transport(*args, **kwargs)
        http.transport = blocked
    print(json.dumps(fixture.resume(job, http=http, max_requests=1)))
