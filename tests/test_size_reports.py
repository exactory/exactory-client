"""Size reports are diagnostics outside immutable scientific records and receipts."""

import contextlib
import io
import json
import math
import unittest

from research_harness import cli, literature, problem_claims
from research_harness.check_mode import check_mutation
from research_harness.evidence import digest
import test_problem_forming_import as imports
from test_research_problem_claims import ProblemCase


class SizeReportTests(unittest.TestCase):
    def test_size_report_uses_only_its_keyed_capacity_witness(self):
        from unittest.mock import patch
        from capacity_evidence_fixtures import build_capacity_runtime
        from research_harness import review_route_capacity as capacity
        route = dict(id='sized', adapter='openai_responses_v1', model='unlisted',
            endpoint='https://example.invalid/responses', configuration=dict(max_output_tokens=1000, timeout_seconds=5),
            runtime=build_capacity_runtime('openai_responses_v1'))
        witness = dict(key=capacity.describe_capacity_key(route), input_tokens=201000)
        records = dict(review_route={'sized': route}, review_route_probe=dict(probe=dict(
            id='probe', route_id='sized', capacity=dict(input_tokens=201000), capacity_evidence=witness)))
        row = capacity.describe_size_report(records, dict(body='Small bounded material.'))['routes'][0]
        self.assertEqual(row['measure']['token_limit'], 200000)
        self.assertTrue(row['measure']['fits'])
        profile = capacity.read_output_profile(records, route)
        profile['verified_input_tokens'] = 1000000000
        route['endpoint'] = 'https://another.example.invalid/responses'
        with patch.object(capacity, 'read_output_profile', return_value=profile):
            row = capacity.describe_size_report(records, dict(body='Small bounded material.'))['routes'][0]
        self.assertEqual(row['measure']['token_limit'], 0)
        self.assertIsNone(row['turns'])
        self.assertFalse(row['measure']['fits'])

    def test_import_check_counts_delivered_bodies_without_changing_the_import_receipt(self):
        fixture = imports.FormingImportTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        source, target = fixture.source, fixture.destination
        body = source.artifacts.put(('Synthetic computation body.\n' * 1000).encode(), 'text/plain')
        from test_research_problem_forming import build_outcome
        from test_research_probe_lifecycle import build_probe_intent, build_probe_outcome
        source.mutate(cli.OPERATIONS['probe-intent'], build_probe_intent(source.artifacts))
        outcome = build_probe_outcome(source.artifacts)
        outcome['stdout'] = body
        source.mutate(cli.OPERATIONS['probe'], outcome)
        source.mutate(cli.OPERATIONS['form-outcome'], build_outcome('with-body', evidence=[dict(
            kind='argument', description='A recorded calculation.', standing='exploratory', date='2026-01-01',
            reference=dict(kind='probe', id='probe-1'), counterexample=None)]))
        payload = dict(id='import-size', source_workspace=str(source.root), source_revision=source.store.revision,
                       source_candidate_ids=['candidate-1'])
        before = target.store.revision
        report = check_mutation(target.store, cli.OPERATIONS['forming-import'], payload,
            expected_revision=before, request_id='check-size', bounded_payload=payload)
        size = report['size_report']
        self.assertGreaterEqual(size['bodies'], 1)
        self.assertGreaterEqual(size['body_characters'], 28000)
        self.assertGreater(size['characters'], size['body_characters'])
        self.assertEqual(size['routes'], [])
        self.assertEqual(size['turns'], 1)
        self.assertEqual(target.store.revision, before)
        receipt = target.mutate(cli.OPERATIONS['forming-import'], payload)
        self.assertNotIn('size_report', receipt)
        stored = target.store.committed_request(receipt['request_id'])['response']
        self.assertEqual(stored, receipt)
        self.assertEqual(target.store.snapshot()['records']['problem_forming_import']['import-size'], receipt['result'])
        moved = source.root.with_name(source.root.name + '-offline')
        source.root.rename(moved)
        self.addCleanup(lambda: moved.rename(source.root))
        self.assertEqual(check_mutation(target.store, cli.OPERATIONS['forming-import'], payload,
            expected_revision=0, request_id=receipt['request_id'], bounded_payload=payload), stored)

    def test_value_receipt_reports_cited_records_and_transitive_bodies_outside_storage(self):
        case = ProblemCase()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.initialize_problem()
        case.select_statement()
        reading = case.record_full_reading()
        payload = case.build_value([reading])
        # A real account reference carries a search record and its captured response.
        search = case.capture_search()
        search['question'] = 'Which earlier statements establish the field boundary?'
        case.mutate(literature.record_search, search)
        source_id = search['responses'][0]['source_id']
        body = case.store.snapshot()['records']['source'][source_id]['response']
        payload['accounts']['search_coverage'] = [dict(id='coverage', purpose='alternatives',
            search_ids=[search['id']], unreached='Unpublished work.')]
        path = case.root / 'value-input.json'
        path.write_text(json.dumps(payload))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cli.main(['problem-value', '--workspace', str(case.root), '--file', str(path),
                      '--expected-revision', str(case.store.revision), '--request-id', 'value-size'])
        receipt = json.loads(output.getvalue())
        size = receipt['size_report']
        self.assertGreater(size['account_records_characters'], len(json.dumps(search)))
        self.assertGreaterEqual(size['body_characters'], len(case.artifacts.read(body).decode('utf-8')))
        self.assertGreater(size['evidence_characters'], size['body_characters'])
        self.assertEqual(size['routes'], [])
        stored = case.store.committed_request('value-size')['response']
        self.assertNotIn('size_report', stored)
        self.assertEqual(stored['result'], receipt['result'])
        self.assertNotIn('size_report', case.store.snapshot()['records']['problem_value'][payload['id']])
        self.assertEqual(digest(stored['result']), digest(receipt['result']))
        (case.root / body['path']).unlink()
        self.assertEqual(problem_claims.record_value(case.store, payload, expected_revision=0,
                                                    request_id='value-size'), stored)

    def test_size_report_uses_each_registered_route_measure_and_warns(self):
        from research_harness import review_route_capacity as capacity
        from capacity_evidence_fixtures import build_capacity_runtime
        route = dict(id='route-size', adapter='openai_responses_v1', model='unlisted', endpoint='https://example.invalid/responses',
                     configuration=dict(max_output_tokens=1000, timeout_seconds=5))
        route['runtime'] = build_capacity_runtime(route['adapter'])
        records = dict(review_route={'route-size': route}, review_route_probe=dict(probe=dict(
            id='probe', route_id='route-size', capacity=dict(input_tokens=151000),
            capacity_evidence=dict(key=capacity.describe_capacity_key(route), input_tokens=151000))))
        report = capacity.describe_size_report(records, {'body': '界' * 60000}, bodies=1)
        row = report['routes'][0]
        request = dict(instructions='', input=[dict(role='user', content=json.dumps({'body': '界' * 60000},
                                                                                  sort_keys=True, ensure_ascii=False))])
        self.assertEqual(row['measure'], capacity.measure_request(route, request, records=records))
        self.assertEqual(row['capacity'], capacity.describe_route_capacity(route, capacity.read_output_profile(records, route),
                                                                       records=records))
        self.assertFalse(row['measure']['fits'])
        self.assertGreaterEqual(row['turns'], math.ceil(row['measure']['tokens_upper'] / row['measure']['token_limit']))
        self.assertIn('warning', report)

    def test_missing_report_body_refuses_before_recording_a_value(self):
        from research_harness.errors import ResearchError
        case = ProblemCase()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.initialize_problem()
        case.select_statement()
        payload = case.build_value([case.record_full_reading()])
        search = case.capture_search()
        search['question'] = 'Which earlier statements establish the field boundary?'
        case.mutate(literature.record_search, search)
        body = case.store.snapshot()['records']['source'][search['responses'][0]['source_id']]['response']
        payload['accounts']['search_coverage'] = [dict(id='coverage', purpose='alternatives',
            search_ids=[search['id']], unreached='Unpublished work.')]
        (case.root / body['path']).unlink()
        before = case.store.revision
        with self.assertRaises(ResearchError) as refused:
            problem_claims.record_value(case.store, payload, expected_revision=before, request_id='missing-size-body')
        self.assertEqual(refused.exception.code, 'artifact_missing')
        self.assertEqual(case.store.revision, before)
        self.assertIsNone(case.store.committed_request('missing-size-body'))

    def test_size_diagnostics_do_not_change_the_process_delivery_mode(self):
        from unittest.mock import patch
        from research_harness import review_protocol, review_route_capacity as capacity
        case = ProblemCase()
        case.setUp()
        self.addCleanup(case.doCleanups)
        original = case.artifacts.put(b'%PDF-1.4\n' + b'0' * 4096 + b'\n%%EOF', 'application/pdf')
        with patch.object(review_protocol.transport, 'text_only_delivery',
                          side_effect=AssertionError('An advisory report changed the process delivery mode')):
            report = capacity.describe_material_size({}, case.artifacts, dict(original=original),
                label=capacity.SIZE_REPORT_IMPORT_LABEL, records_field='import_records_characters')
        self.assertEqual(report['binary_bodies'], 1)
        self.assertEqual(report['text_bodies'], 0)
        self.assertEqual(report['body_characters'], 0)
        self.assertLess(report['evidence_characters'], original['size'])
