"""Integrated jobs preserve recorded history, expenditure and evidence boundaries."""
import copy
import json
import os
import subprocess
import sys
import time
from unittest.mock import patch

import test_research_fulltext_jobs as jobs_fixtures
from research_harness import acquisition, review_source_complete, review_delivered_evidence
from research_harness.artifacts import ArtifactStore
from research_fixtures import client, xml_response, atom, entry


class AcquisitionCompatibilityTests(jobs_fixtures.JobCase):
    def test_job_with_historical_part_and_staged_records_preserves_receipts(self):
        self.load_jobs()
        import test_review_staged_scale_entry as staged_fixtures
        from research_harness import review_protocol as protocol
        fixture = staged_fixtures.StagedScaleEntryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.oversized(count=6, characters=24000, limit=110000)
        part = fixture.assign('historical-part')
        fixture.invoke(part)
        fixture.install_scale_packet(large=False)
        controller = fixture.case.assign('result', 'first', identifier='historical-staged',
                                         staged_upgrade={'assignment_id': part['id']})
        self.assertIn('staged_review', controller)
        self.store, self.artifacts, self.root = fixture.case.store, fixture.case.artifacts, fixture.case.root
        from research_harness.storage import _canonical
        from research_harness.evaluation import Evaluation
        before = self.store.snapshot()['records']
        attempts = list(before['review_attempt'].values())
        originals = {attempt['id']: self.artifacts.read(attempt['request']) for attempt in attempts}
        rebuilt = {attempt['assignment_id']: _canonical(protocol.build_assignment_request(
            before, self.artifacts, before['review_assignment'][attempt['assignment_id']])) for attempt in attempts}
        tools_root = self.root
        jobs_fixtures.extraction_fixtures.ExtractionUnitsTests.create_tools(self, pages=3)
        with patch.dict(os.environ, PATH=str(tools_root) + os.pathsep + os.environ['PATH']):
            http, _, _ = client([xml_response(atom([entry()], total=1))])
            acquisition.acquire_work(self.store, jobs_fixtures.VERSION, request_id='job-metadata',
                                     expected_revision=self.store.revision, http=http)
            self.finalize_large_source()
        after = self.store.snapshot()['records']
        for attempt in attempts:
            assignment = after['review_assignment'][attempt['assignment_id']]
            self.assertTrue(protocol.authenticate_attempt(after, Evaluation(after, self.artifacts), assignment, attempt))
            self.assertEqual(self.artifacts.read(attempt['request']), originals[attempt['id']])
            self.assertEqual(_canonical(protocol.build_assignment_request(after, self.artifacts, assignment)),
                             rebuilt[assignment['id']])

    def test_process_resume_with_metadata_mutation_keeps_progress_and_identity(self):
        self.load_jobs()
        self.store.mutate('fixture.account', {}, lambda tx: tx.put('configuration', 'research', {'profile': 'research'}),
                          request_id='account', expected_revision=self.store.revision)
        job = self.start()
        http, calls = self.wire()
        self.resume(job, http=http, max_requests=1)
        barrier = str(self.root / 'barrier')
        script = "from test_research_fulltext_jobs import transfer_worker; transfer_worker(%r, %r, %r)" % (str(self.root), job, barrier)
        worker = subprocess.Popen([sys.executable, '-c', script], env=dict(os.environ, PYTHONPATH='tests:.'),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: worker.kill() if worker.poll() is None else None)
        deadline = time.monotonic() + 20
        while not (self.root / 'barrier.ready').exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue((self.root / 'barrier.ready').exists())
        http, _, _ = client([xml_response(atom([entry('2601.00002v1')], total=1))])
        acquisition.acquire_work(self.store, '2601.00002v1', request_id='concurrent-metadata',
                                 expected_revision=self.store.revision, http=http)
        (self.root / 'barrier.release').write_text('release')
        output, errors = worker.communicate(timeout=60)
        self.assertEqual(worker.returncode, 0, errors)
        status = self.load_jobs().read_job_status(self.store, job)
        self.assertEqual(status['attempts_used'], 2)
        self.assertEqual(status['transfer_units'], 2)
        result = self.resume(job, http=self.wire()[0])
        self.assertEqual(result['attempts_used'], 3)
        for _ in range(2):
            result = self.resume(job)
        self.assertEqual(result['status'], 'complete')
        capture = self.store.snapshot()['records']['work'][jobs_fixtures.VERSION]['fulltexts'][-1]
        self.assertEqual(self.artifacts.read(capture['original']), jobs_fixtures.BODY)
        from research_harness.resources import account_report
        account = account_report(self.store.snapshot()['records'], 'research')['literature']
        self.assertEqual(account['network_requests']['charged'], 4)
        self.assertEqual(account['network_requests']['reserved'], 0)
        self.assertEqual(account['source_bytes']['charged'], len(jobs_fixtures.BODY) + len(atom([entry('2601.00002v1')], total=1)))

    def test_capture_failure_grants_no_complete_reading_or_delivery_credit(self):
        from research_harness.source_links import captured_source
        from research_harness.errors import ResearchError
        self.load_jobs()
        job = self.start()
        self.resume(job, http=self.wire()[0], max_requests=1)
        records = self.store.snapshot()['records']
        sources = [s for s in records['source'].values() if s.get('provider') == 'fulltext']
        self.assertTrue(sources)
        for source in sources:
            with self.assertRaises(ResearchError):
                captured_source(records, self.artifacts, source['id'])
        self.assertEqual(records['work'][jobs_fixtures.VERSION]['fulltexts'], [])
        self.assertNotIn('reading', records)
        self.assertEqual(review_source_complete.selection(records, self.artifacts, [])['sources'], [])
