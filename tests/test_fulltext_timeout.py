"""A stalled full-text call reports the network limit and its received prefix."""

import io
import socket
from unittest.mock import patch

from research_harness import acquisition, fulltext_transfer
from research_harness.http import HttpFailure, RawResponse, SystemClock
from research_fixtures import Clock, make_http_client
from test_research_fulltext_jobs import JobCase, VERSION, URL


class FulltextTimeoutTests(JobCase):
    def stall(self, *, prefix=None, status=200, max_retries=0, **limits):
        clock = Clock()
        calls = []

        class StalledStream(io.BytesIO):
            def read(self, amount):
                if self.tell() == len(self.getvalue()):
                    clock.sleep(calls[-1])
                    raise socket.timeout()
                return super().read(amount)

        def transport(url, headers, *, address, timeout):
            calls.append(timeout)
            if prefix is None:
                clock.sleep(timeout)
                raise socket.timeout()
            response_headers = {'Content-Type': 'application/pdf'}
            if status == 206:
                response_headers.update({'ETag': '"original"', 'Content-Range': 'bytes 0-39/105'})
            return RawResponse(status, response_headers, StalledStream(prefix), address)

        http = make_http_client(transport=transport, resolver=lambda host, timeout: ['93.184.216.34'],
                                clock=clock, max_retries=max_retries, **limits)
        return http, calls

    def test_fulltext_stall_before_first_byte_reports_attempt_limit_elapsed_and_bytes(self):
        http, calls = self.stall()
        result = acquisition.acquire_fulltext(self.store, VERSION, URL, request_id='stalled',
            expected_revision=self.store.revision, http=http)
        self.assertEqual(result['pending'], [{'code': 'timeout', 'timeout_seconds': 30,
                                             'elapsed_seconds': 30, 'bytes_received': 0}])
        self.assertEqual(calls, [30])

    def test_fulltext_timeout_reports_received_prefix(self):
        http, _ = self.stall(prefix=b'%PDF-', timeout=7)
        result = acquisition.acquire_fulltext(self.store, VERSION, URL, request_id='prefix',
            expected_revision=self.store.revision, http=http)
        self.assertEqual(result['pending'], [{'code': 'timeout', 'timeout_seconds': 7,
                                             'elapsed_seconds': 7, 'bytes_received': 5}])
        self.assertEqual(self.artifacts.read(result['capture']['original']), b'%PDF-')

    def test_fulltext_total_deadline_after_stalled_retry_keeps_attempt_diagnostics(self):
        http, calls = self.stall(total_timeout=30, max_retries=2)
        result = acquisition.acquire_fulltext(self.store, VERSION, URL, request_id='total-deadline',
            expected_revision=self.store.revision, http=http)
        self.assertEqual(result['pending'], [{'code': 'timeout', 'timeout_seconds': 30,
                                             'elapsed_seconds': 30, 'bytes_received': 0}])
        self.assertEqual(calls, [30])

    def test_total_deadline_describes_latest_attempt_instead_of_prior_timeout(self):
        clock = Clock()
        calls = []

        def transport(url, headers, *, address, timeout):
            calls.append(timeout)
            if len(calls) == 1:
                clock.sleep(2)
                raise socket.timeout()
            clock.sleep(0.9)
            return RawResponse(503, {'Content-Type': 'application/pdf'}, io.BytesIO(b'busy'), address)

        http = make_http_client(transport=transport, resolver=lambda host, timeout: ['93.184.216.34'],
                                clock=clock, total_timeout=4.2, max_retries=2)
        result = acquisition.acquire_fulltext(self.store, VERSION, URL, request_id='last-attempt',
            expected_revision=self.store.revision, http=http)
        pending = result['pending'][0]
        self.assertEqual(pending['code'], 'timeout')
        self.assertAlmostEqual(pending['timeout_seconds'], calls[-1])
        self.assertAlmostEqual(pending['elapsed_seconds'], 0.9, places=5)
        self.assertEqual(pending['bytes_received'], 4)

    def test_fulltext_server_stalls_before_first_body_byte(self):
        server, connection = socket.socketpair()
        self.addCleanup(server.close)
        self.addCleanup(connection.close)

        def transport(url, headers, *, address, timeout):
            connection.settimeout(timeout)
            return RawResponse(200, {'Content-Type': 'application/pdf'}, connection.makefile('rb'), address)

        http = make_http_client(transport=transport, resolver=lambda host, timeout: ['93.184.216.34'],
                                clock=SystemClock(), timeout=0.02, max_retries=0)
        result = acquisition.acquire_fulltext(self.store, VERSION, URL, request_id='socket-stall',
            expected_revision=self.store.revision, http=http)
        self.assertEqual(result['pending'][0]['code'], 'timeout')
        self.assertEqual(result['pending'][0]['timeout_seconds'], 0.02)
        self.assertGreaterEqual(result['pending'][0]['elapsed_seconds'], 0.02)
        self.assertEqual(result['pending'][0]['bytes_received'], 0)

    def test_resume_stall_reports_remaining_invocation_limit_and_received_range_prefix(self):
        job = self.start(allowances={'invocation_seconds': 12, 'range_bytes': 40})
        http, calls = self.stall(prefix=b'%PDF-', status=206)
        result = self.resume(job, http=http)
        self.assertEqual(result['pending'], [{'code': 'timeout', 'timeout_seconds': 12,
                                             'elapsed_seconds': 12, 'bytes_received': 5}])
        self.assertEqual(calls, [12])
        status = self.load_jobs().read_job_status(self.store, job)
        self.assertEqual(status['pending'], result['pending'])
        self.assertEqual(status['next_unit'], 'byte:0')

    def test_resume_stall_before_first_byte_reports_fixed_cap_with_larger_invocation_allowance(self):
        job = self.start(allowances={'invocation_seconds': 90})
        http, calls = self.stall()
        result = self.resume(job, http=http)
        self.assertEqual(result['pending'], [{'code': 'timeout', 'timeout_seconds': 30,
                                             'elapsed_seconds': 30, 'bytes_received': 0}])
        self.assertEqual(calls, [30])

    def test_resume_assembly_deadline_reports_invocation_limit_and_received_bytes(self):
        job = self.start(allowances={'invocation_seconds': 12, 'range_bytes': 40})
        http, _ = self.wire()
        started = http.clock.monotonic()
        read = fulltext_transfer._RangeReader.read

        def stalled_read(reader, amount):
            http.clock.sleep(12)
            return read(reader, amount)

        with patch.object(fulltext_transfer._RangeReader, 'read', stalled_read):
            result = self.resume(job, http=http)
        self.assertEqual(result['pending'], [{'code': 'timeout', 'timeout_seconds': 12,
            'elapsed_seconds': http.clock.monotonic() - started, 'bytes_received': 105}])

    def test_legacy_timeout_receipt_and_source_stay_unchanged_after_new_timeout(self):
        http, _ = self.stall()
        original_get = http.get

        def raise_legacy_timeout(*args, **kwargs):
            try:
                original_get(*args, **kwargs)
            except HttpFailure as error:
                raise HttpFailure(error.code, error.message, error.attempts)

        http.get = raise_legacy_timeout
        historical = acquisition.acquire_fulltext(self.store, VERSION, URL, request_id='old-timeout',
            expected_revision=self.store.revision, http=http)
        self.assertEqual(historical['pending'], [{'code': 'timeout'}])
        before = self.store.snapshot()
        old_source = before['records']['source'][historical['capture']['source_id']]
        new = acquisition.acquire_fulltext(self.store, VERSION, URL, request_id='new-timeout',
            expected_revision=self.store.revision, http=self.stall()[0])
        self.assertEqual(new['pending'][0]['timeout_seconds'], 30)
        replay = acquisition.acquire_fulltext(self.store, VERSION, URL, request_id='old-timeout',
            expected_revision=before['revision'], http=self.stall()[0])
        self.assertEqual(replay, historical)
        self.assertEqual(self.store.snapshot()['records']['source'][old_source['id']], old_source)
