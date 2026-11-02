"""Prospective source replies preserve exact pending work and route capacity."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from research_harness import bar_sources, review_protocol as protocol, review_transport as transport
from research_harness import review_excerpts
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from research_harness.review_codex_transport import packet_text
from strategy_fixtures import StrategyCase
import test_research_bar_sources as sources
import test_research_complete_source_continuation as complete_sources


class SourceReplyBatchTests(StrategyCase):
    metadata = sources.BarSourceTests.metadata
    capture = sources.BarSourceTests.capture
    assign = sources.BarSourceTests.assign
    packet = sources.BarSourceTests.packet
    invoke = sources.BarSourceTests.invoke
    request = sources.BarSourceTests.request

    def setUp(self):
        super().setUp()
        self.sequence = 0
        self.setup_dossier()
        self.route()

    def fit_locally(self, limit):
        original = transport.request_body

        def build(route, prompt, packet, history=(), **kwargs):
            return original(dict(route, adapter='codex_cli_v1', endpoint='codex://local'),
                            prompt, packet, history, **kwargs)

        for patcher in (patch.object(transport, 'request_body', side_effect=build),
                        patch.object(transport, 'local_input_limit', return_value=limit),
                        patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', limit)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def read_request(self, identifier):
        records = self.store.snapshot()['records']
        return protocol.build_assignment_request(records, self.artifacts, records['review_assignment'][identifier])

    def assert_batch(self, identifier, requests, limit):
        packet = self.packet(identifier)
        delivered = [row['request'] for row in packet['source_deliveries'] if row['status'] == 'delivered']
        self.assertEqual(delivered, requests[:1])
        self.assertEqual(packet['pending_source_requests'], requests[1:])
        actual = self.read_request(identifier)
        self.assertLessEqual(len(json.dumps(actual, sort_keys=True, ensure_ascii=False)), limit)
        self.assertLessEqual(len(packet_text(actual)), limit)
        marker = packet['source_reply_batch']
        self.assertEqual(marker['protocol'], 'exactory-review-source-batch-v1')
        self.assertEqual(marker['included_request_ids'], [requests[0]['id']])
        self.assertEqual(marker['deferred_request_ids'], [requests[1]['id']])
        self.assertEqual(marker['measurement']['serialized_characters'],
                         len(json.dumps(actual, sort_keys=True, ensure_ascii=False)))
        return packet

    def test_inventory_replies_fit_separately_and_deferred_request_persists(self):
        from research_harness.acquisition import import_response
        from research_fixtures import atom, entry
        rows = [entry('2601.%05dv1' % n).replace('<title>An authored example</title>',
                '<title>' + ('First' if n == 1 else '長い題名' * 5000) + '</title>')
                for n in (1, 2, 3)]
        import_response(self.store, 'arxiv', atom(rows, total=3, size=3),
            source_url='https://export.arxiv.org/api/query?search_query=fixture',
            captured_at='2026-09-07T12:00:00Z', expected_revision=self.store.revision, request_id='inventory')
        with patch.object(bar_sources, 'PAGE_SIZE', 1):
            self.assign()
        requests = [{'id': 'page-1', 'kind': 'inventory_page', 'page': 1},
                    {'id': 'query-2', 'kind': 'inventory_query', 'query': '2601.00003', 'page': 0}]
        self.request(requests)
        self.fit_locally(48000)
        self.assign('batch', prior_assignment_id='a')
        self.assert_batch('batch', requests, 48000)
        self.invoke(self.review_response('bar', status='unresolved'), 'batch')
        self.assign('next', prior_assignment_id='batch')
        self.assertEqual([row['request'] for row in self.packet('next')['source_deliveries']], requests[1:])
        self.assertEqual(self.packet('next')['pending_source_requests'], [])

    def test_aggregate_abstract_replies_are_batched(self):
        versions = [self.metadata(n, abstract=('Exact abstract %d. ' % n) * 1500) for n in (1, 2)]
        self.assign()
        requests = [{'id': 'abstract-%d' % n, 'kind': 'source', 'version_id': version, 'depth': 'abstract'}
                    for n, version in enumerate(versions)]
        self.request(requests)
        self.fit_locally(54000)
        self.assign('batch', prior_assignment_id='a')
        self.assert_batch('batch', requests, 54000)

    def test_deferred_passage_is_pending_and_consumes_no_allowance(self):
        version = self.metadata(1, abstract='Exact abstract. ' * 1700)
        self.capture(version, body='Passage anchor with the exact proof. ' * 100)
        self.assign()
        self.request([{'id': 'prime', 'kind': 'inventory_page', 'page': 0}])
        self.assign('primed', prior_assignment_id='a')
        requests = [{'id': 'abstract', 'kind': 'source', 'version_id': version, 'depth': 'abstract'},
                    {'id': 'passage', 'kind': 'passage', 'version_id': version,
                     'anchor': 'Passage anchor with the exact proof.', 'characters': 3000}]
        self.request(requests, 'primed')
        self.fit_locally(53000)
        self.assign('batch', prior_assignment_id='primed')
        packet = self.assert_batch('batch', requests, 53000)
        self.assertEqual(packet[review_excerpts.KEY]['allowances']['passages']['used'], 0)
        self.invoke(self.review_response('bar', status='unresolved'), 'batch')
        with patch.object(transport, 'local_input_limit', return_value=100000):
            self.assign('next', prior_assignment_id='batch')
        self.assertEqual(self.packet('next')[review_excerpts.KEY]['allowances']['passages']['used'], 3000)
        self.assertEqual(self.packet('next')['pending_source_requests'], [])

    def test_unchanged_unavailable_request_with_a_new_id_refuses_without_a_call(self):
        self.assign()
        request = {'id': 'missing', 'kind': 'source', 'version_id': 'arxiv:2601.99999v1', 'depth': 'fulltext'}
        self.request([request])
        self.assign('unavailable', prior_assignment_id='a')
        self.request([dict(request, id='renamed')], 'unavailable')
        revision = self.store.revision
        with patch.object(transport, 'send_request', side_effect=AssertionError('No reviewer call is permitted')):
            with self.assertRaises(ResearchError) as caught:
                self.assign('repeated', prior_assignment_id='unavailable')
        self.assertEqual(caught.exception.code, 'review_source_no_progress')
        self.assertEqual(self.store.revision, revision)
        self.assertNotIn('repeated', self.store.snapshot()['records']['review_assignment'])
        self.assertEqual(self.packet('unavailable')['pending_source_requests'], [request])

    def test_repeating_inventory_metadata_cannot_restart_an_unavailable_request(self):
        self.assign()
        missing = {'id': 'missing', 'kind': 'source', 'version_id': 'arxiv:2601.99999v1', 'depth': 'fulltext'}
        self.request([missing])
        self.assign('unavailable', prior_assignment_id='a')
        page = {'id': 'page', 'kind': 'inventory_page', 'page': 0}
        self.request([page], 'unavailable')
        self.assign('inventory', prior_assignment_id='unavailable')
        self.request([dict(page, id='renamed-page')], 'inventory')
        with self.assertRaises(ResearchError) as caught:
            self.assign('repeated', prior_assignment_id='inventory')
        self.assertEqual(caught.exception.code, 'review_source_no_progress')

    def test_irreducible_reply_retains_exact_request_and_limiting_component(self):
        version = self.metadata(1, abstract='Indivisible exact abstract. ' * 2500)
        self.assign()
        request = {'id': 'large', 'kind': 'source', 'version_id': version, 'depth': 'abstract'}
        self.request([request])
        self.fit_locally(24000)
        with self.assertRaises(ResearchError) as caught:
            self.assign('too-large', prior_assignment_id='a')
        self.assertEqual(caught.exception.code, 'review_input_too_large')
        self.assertEqual(caught.exception.details['source_request'], request)
        self.assertEqual(caught.exception.details['limiting_component'], 'source_reply')
        self.assertNotIn('too-large', self.store.snapshot()['records']['review_assignment'])

    def test_native_attachment_bytes_are_in_the_frozen_measurement(self):
        version = self.metadata(1)
        self.capture(version, pdf_text='An exact native PDF proof and its construction.\f')
        self.assign()
        self.request([{'id': 'pdf', 'kind': 'source', 'version_id': version, 'depth': 'fulltext'}])
        self.assign('pdf-reply', prior_assignment_id='a')
        actual = self.read_request('pdf-reply')
        content = actual['input'][-1]['content']
        self.assertIn('input_file', [part['type'] for part in content])
        self.assertIn('source_reply_batch', self.packet('pdf-reply'))
        measured = self.packet('pdf-reply')['source_reply_batch']['measurement']
        self.assertEqual(measured['serialized_utf8_bytes'],
                         len(json.dumps(actual, sort_keys=True, ensure_ascii=False).encode('utf-8')))
        script = (
            "import json, sys; from research_harness.storage import Store; "
            "from research_harness.review_protocol import build_assignment_request; "
            "store=Store(sys.argv[1]); records=store.snapshot()['records']; "
            "print(json.dumps(build_assignment_request(records, store.build_artifact_store(), "
            "records['review_assignment']['pdf-reply']), sort_keys=True))")
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
                           PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        rebuilt = subprocess.check_output([sys.executable, '-c', script, str(self.root)],
                                          cwd='/tmp', env=environment)
        self.assertEqual(json.loads(rebuilt), actual)

    def test_missing_native_attachment_cannot_count_as_a_delivered_reply(self):
        version = self.metadata(1)
        self.capture(version, pdf_text='An exact native PDF proof and its construction.\f')
        self.assign()
        request = {'id': 'pdf', 'kind': 'source', 'version_id': version, 'depth': 'fulltext'}
        self.request([request])
        original = transport.request_body

        def omit_attachment(*args, **kwargs):
            actual = original(*args, **kwargs)
            content = actual['input'][-1]['content']
            if isinstance(content, list):
                actual['input'][-1]['content'] = content[:1]
            return actual

        with patch.object(transport, 'request_body', side_effect=omit_attachment):
            with self.assertRaises(ResearchError) as caught:
                self.assign('missing-pdf', prior_assignment_id='a')
        self.assertEqual(caught.exception.code, 'review_input_too_large')
        self.assertEqual(caught.exception.details['source_request'], request)

    def test_missing_reply_provenance_cannot_count_as_complete_delivery(self):
        version = self.metadata(1)
        self.assign()
        request = {'id': 'abstract', 'kind': 'source', 'version_id': version, 'depth': 'abstract'}
        self.request([request])
        original = transport.request_body

        def omit_provenance(*args, **kwargs):
            actual = original(*args, **kwargs)
            content = actual['input'][-1]['content']
            packet = json.loads(content[len(transport.PACKET_LABEL):])
            for row in packet.get('source_deliveries', []):
                row.pop('source_provenance', None)
            actual['input'][-1]['content'] = transport.PACKET_LABEL + json.dumps(
                packet, sort_keys=True, ensure_ascii=False)
            return actual

        with patch.object(transport, 'request_body', side_effect=omit_provenance):
            with self.assertRaises(ResearchError) as caught:
                self.assign('missing-provenance', prior_assignment_id='a')
        self.assertEqual(caught.exception.code, 'review_input_too_large')
        self.assertEqual(caught.exception.details['source_request'], request)

    def test_deferred_passage_does_not_prevent_a_later_selected_answer(self):
        from research_harness.review_source_batches import project_replies
        version = self.metadata(1)
        self.capture(version, body='Passage anchor with the exact proof. ' * 100)
        records = self.store.snapshot()['records']
        requests = [{'id': name, 'kind': 'passage', 'version_id': version,
                     'anchor': 'Passage anchor with the exact proof.', 'characters': 3000}
                    for name in ('first', 'second')]
        allowances = review_excerpts.create_allowances()
        allowances['passages']['characters'] = 4000
        answers, after = review_excerpts.answer_passages(records, self.artifacts, requests, allowances)
        self.assertEqual(answers[1]['status'], 'over_allowance')
        packet = {'source_deliveries': answers, 'pending_source_requests': [],
                  review_excerpts.KEY: {'allowances': after}, 'evidence': []}
        projected = project_replies(records, self.artifacts, packet, {1}, allowances)
        self.assertEqual(projected['source_deliveries'][1]['status'], 'delivered')
        self.assertEqual(len(projected['source_deliveries'][1]['text']), 3000)
        self.assertEqual(projected['pending_source_requests'], requests[:1])
        self.assertEqual(projected[review_excerpts.KEY]['allowances']['passages']['used'], 3000)

    def test_recorded_legacy_packet_and_request_do_not_acquire_batching(self):
        self.metadata(1)
        assignment = self.assign()
        records = self.store.snapshot()['records']
        packet_bytes = self.artifacts.read(assignment['packet'])
        request = self.read_request('a')
        self.invoke(self.review_response('bar', status='unresolved'))
        records = self.store.snapshot()['records']
        attempt = records['review_attempt']['attempt-a']
        self.assertEqual(protocol.build_assignment_request(records, self.artifacts, assignment), request)
        self.assertEqual(self.artifacts.read(assignment['packet']), packet_bytes)
        self.assertNotIn('source_reply_batch', json.loads(packet_bytes))
        self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, assignment, attempt))
        self.assertEqual(digest(json.loads(self.artifacts.read(attempt['request']))), digest(request))
        # Reconstruct an old continuation whose packet predates the planner.
        legacy_packet = dict(json.loads(packet_bytes), source_deliveries=[{
            'request': {'id': 'old-page', 'kind': 'inventory_page', 'page': 0},
            'status': 'delivered', 'reading_credit': False,
            'page': json.loads(packet_bytes)['source_inventory']['first_page']}],
            pending_source_requests=[], continuation={'assignment_id': 'a'})
        legacy_packet.pop('digest')
        legacy_packet['digest'] = digest(legacy_packet)
        frozen_bytes = json.dumps(legacy_packet, sort_keys=True, ensure_ascii=False).encode('utf-8')
        legacy = dict(assignment, id='legacy', packet=self.artifacts.put(frozen_bytes, 'application/json'),
                      packet_digest=legacy_packet['digest'], history=request['input'])
        original = transport.request_body(records['review_route']['route'], legacy['prompt']['text'],
                                          legacy_packet, legacy['history'])
        records['review_assignment']['legacy'] = legacy
        records['review_attempt']['legacy-attempt'] = {
            'id': 'legacy-attempt', 'assignment_id': 'legacy', 'status': 'completed',
            'request': self.artifacts.put(json.dumps(original).encode('utf-8'), 'application/json')}
        self.assertEqual(protocol.build_assignment_request(records, self.artifacts, legacy), original)
        self.assertEqual(self.artifacts.read(legacy['packet']), frozen_bytes)
        self.assertNotIn('source_reply_batch', legacy_packet)


class SharedReplyProjectionTests(unittest.TestCase):
    def test_measurement_feedback_refuses_instead_of_looping(self):
        from research_harness import review_source_batches as batches
        from test_review_html_delivery import ROUTE, entry
        previous = dict(protocol=bar_sources.PROTOCOL, role='bar', reviewer_id='r', dossier_id='d',
                        evidence=[entry('h' * 5000, 'text/plain')], pending_source_requests=[])
        previous['digest'] = digest(previous)
        history = [{'role': 'user', 'content': transport.PACKET_LABEL +
                    json.dumps(previous, sort_keys=True, ensure_ascii=False)}]
        request = {'id': 'page', 'kind': 'inventory_page', 'page': 0}
        packet = dict(protocol=bar_sources.PROTOCOL, role='bar', reviewer_id='r', dossier_id='d',
            continuation={'assignment_id': 'prior'}, evidence=[], pending_source_requests=[], padding='',
            source_deliveries=[dict(request=request, status='delivered', page={'items': []}, reading_credit=False)])
        records = {'review_assignment': {'prior': {'id': 'prior'}}}
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 10000), \
                patch.object(protocol, 'source_delivery_packet', return_value=previous):
            first = batches.plan_packet(records, None, ROUTE, packet, {'text': 'JSON'}, history)
            # Put the unelided request exactly at the boundary. Its own measured
            # counts then gain a digit, triggering history elision and a cycle.
            packet['padding'] = 'x' * (10000 - first[batches.MARKER]['measurement']['rendered_characters'])
            original = batches.measure_request
            steps = []

            def measure(*args):
                measured = original(*args)
                steps.append(measured)
                if len(steps) > 16:
                    self.fail('Request measurement did not stabilize or refuse.')
                return measured

            with patch.object(batches, 'measure_request', side_effect=measure):
                with self.assertRaises(ResearchError) as caught:
                    batches.plan_packet(records, None, ROUTE, packet, {'text': 'JSON'}, history)
            self.assertEqual(caught.exception.code, 'review_input_too_large')
            self.assertEqual(caught.exception.details['source_request'], request)
            self.assertEqual(caught.exception.details['limiting_component'], 'source_reply_measurement')

    def test_visual_measurement_counts_native_image_wrappers(self):
        from research_harness import review_codex_visual_transport as visual, review_visual_inputs
        from research_harness.review_source_batches import measure_request
        route = {'adapter': 'codex_cli_visual_v1', 'model': 'fixture',
                 'configuration': {'max_output_tokens': 1000}}
        request = review_visual_inputs.probe_request(route, 'Review JSON.', {
            'allowed_control': 'a' * 32, 'evidence_control': 'b' * 32, 'evidence': []})
        directory = str((Path(os.environ.get('TMPDIR') or tempfile.gettempdir()) /
                         'review-visual-00000000').resolve())
        parts = visual.native_user_parts(request, directory)
        expected = sum(len(part['text']) for part in parts if part['type'] == 'input_text')
        measured = measure_request(route, request)
        self.assertEqual(measured['rendered_characters'], expected)
        self.assertGreater(measured['serialized_characters'], expected)

    def test_repeating_a_partial_reply_while_the_same_replies_are_deferred_refuses(self):
        from research_harness.artifacts import ArtifactStore
        from research_harness.review_source_batches import plan_packet
        import test_review_source_capacity as legacy
        from test_review_html_delivery import ROUTE
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(Path(directory))
            packet = legacy.source_packet()
            packet.update(protocol=bar_sources.PROTOCOL, dossier_id='fixture', reviewer_id='reviewer')
            packet['source_deliveries'][0]['status'] = 'partial'
            packet['pending_source_requests'] = [packet['source_deliveries'][0]['request']]
            for entry, row in zip(packet['evidence'], packet['source_deliveries']):
                reference = artifacts.put(entry['content'].encode(), 'text/plain')
                entry['artifact'] = reference
                row['evidence'] = [reference]
            packet['digest'] = digest(packet)
            records = {'review_assignment': {'prior': {'id': 'prior'}}}
            with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 60000):
                with patch.object(protocol, 'source_delivery_packet', return_value={}):
                    first = plan_packet(records, artifacts, ROUTE, packet, {'text': 'Review exact JSON.'}, [])
                self.assertEqual([row['status'] for row in first['source_deliveries']],
                                 ['partial', 'deferred_capacity', 'deferred_capacity'])
                with patch.object(protocol, 'source_delivery_packet', return_value=json.loads(json.dumps(first))):
                    with self.assertRaises(ResearchError) as caught:
                        plan_packet(records, artifacts, ROUTE, packet, {'text': 'Review exact JSON.'}, [])
                self.assertEqual(caught.exception.code, 'review_source_no_progress')

    def test_deferred_sources_preserve_a_body_shared_with_a_selected_reply(self):
        from research_harness.review_source_batches import project_replies
        import test_review_source_capacity as legacy
        packet = legacy.source_packet()
        shared = packet['evidence'][0]
        packet['source_deliveries'][1]['evidence'].append(shared['artifact'])
        projected = project_replies({}, None, packet, {0}, None)
        self.assertEqual(projected['evidence'][0], shared)
        self.assertEqual(projected['evidence'][1]['content'], '')
        self.assertEqual(projected['pending_source_requests'],
                         [row['request'] for row in packet['source_deliveries'][1:]])


class CompleteVisiblePreflightTests(unittest.TestCase):
    def test_new_complete_visible_closure_can_restore_delivery_while_a_source_is_unavailable(self):
        fixture = complete_sources.NativeCompleteSourceTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.assign('ordinary')
        fixture.invoke('ordinary', fixture.output([fixture.second_request]))
        fixture.assign('unavailable', prior='ordinary')
        fixture.invoke('unavailable', fixture.output([]))
        assignment = fixture.assign('complete', prior='unavailable', source_delivery='complete-visible-v1')
        packet = fixture.packet(assignment)
        self.assertEqual(packet['pending_source_requests'], [fixture.second_request])
        self.assertTrue(any(row['request'] == fixture.request and row['status'] == 'delivered'
                            for row in packet['source_deliveries']))

    def test_oversized_complete_visible_assignment_fails_before_admission(self):
        fixture = complete_sources.NativeCompleteSourceTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.fixture.capture(fixture.version, pdf_text='Complete visible exact proof. ' * 15000 + '\f')
        revision = fixture.store.revision
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 30000):
            with patch.object(transport, 'send_request', side_effect=AssertionError('No reviewer call is permitted')):
                with self.assertRaises(ResearchError) as caught:
                    fixture.assign('oversized', source_delivery='complete-visible-v1')
        self.assertEqual(caught.exception.code, 'review_input_too_large')
        self.assertEqual(caught.exception.details['limiting_component'], 'complete_visible_closure')
        self.assertEqual(fixture.store.revision, revision)
        self.assertNotIn('oversized', fixture.store.snapshot()['records']['review_assignment'])
