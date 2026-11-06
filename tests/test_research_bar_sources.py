"""Public reviewer requests preserve complete source access without bulk delivery."""

import json
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from strategy_fixtures import StrategyCase
from research_harness.review_bodies import read_history
from research_harness import research_decisions, review_protocol as protocol
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.reading import record_reading
from research_harness.reading import record_availability
from availability_fixtures import registry_absence, terminal_absence


class BarSourceTests(StrategyCase):
    metadata = LiteratureCase.metadata
    capture = LiteratureCase.capture
    span = LiteratureCase.span
    link = LiteratureCase.link
    abstract_note = LiteratureCase.abstract_note
    def cohort(self, numbers):
        from research_harness.acquisition import import_response
        from research_fixtures import atom, entry
        values = [entry('2601.%05dv1' % n, abstract='Source %d studies bounded sequences and reports a finite example.' % n) for n in numbers]
        import_response(self.store, 'arxiv', atom(values, total=len(values), size=len(values)),
            source_url='https://export.arxiv.org/api/query?search_query=fixture', captured_at='2026-09-07T12:00:00Z',
            expected_revision=self.store.revision, request_id='bulk-fixture')

    def setUp(self):
        super().setUp()
        self.sequence = 0
        self.setup_dossier()
        self.route()

    def assign(self, identifier='a', reviewer='reviewer-a', **context):
        return self.mutate(protocol.record_assignment, {'id': identifier, 'route_id': 'route',
            'dossier_id': 'dossier-1', 'role': 'bar', 'reviewer_id': reviewer,
            'author_id': 'author', 'context': context})['result']

    def packet(self, identifier='a'):
        assignment = self.store.snapshot()['records']['review_assignment'][identifier]
        return json.loads(self.artifacts.read(assignment['packet']))

    def invoke(self, response, assignment='a'):
        with patch('research_harness.review_transport.send_request', return_value=self.provider_response(response)):
            return protocol.invoke_assignment(self.store, {'id': 'attempt-' + assignment, 'assignment_id': assignment},
                expected_revision=self.store.revision, request_id='invoke-' + assignment, credential='fixture-only')

    def request(self, requests, assignment='a'):
        response = self.review_response('bar', status='unresolved')
        response['value']['evidence'] = []
        response['source_requests'] = requests
        self.invoke(response, assignment)
        return response

    def qualified_continuation(self, availability, depth='abstract'):
        self.mutate(record_availability, availability)
        self.assign()
        request = {'id': 'qualified', 'kind': 'source', 'version_id': availability['version_id'], 'depth': depth}
        self.request([request])
        self.assign('qualified', prior_assignment_id='a')
        packet = self.packet('qualified')
        return request, packet, packet['source_deliveries'][0]

    def test_registry_absence_delivers_qualified_evidence(self):
        availability = registry_absence(self)
        request, packet, delivery = self.qualified_continuation(availability)
        self.assertEqual(delivery['status'], 'unavailable_qualified')
        self.assertEqual(delivery['request'], request)
        self.assertEqual(delivery['availability'], dict(availability, qualification='noncritical_only'))
        self.assertNotIn('qualification', self.store.snapshot()['records']['availability'][availability['id']])
        self.assertEqual(packet['pending_source_requests'], [])
        self.assertFalse(delivery['reading_credit'])
        self.assertFalse(delivery['scientific_truth_certified'])
        records = self.store.snapshot()['records']
        self.assertEqual(delivery['evidence'], [records['source'][s]['response'] for s in availability['source_ids']])
        self.assertEqual({p['provider'] for p in delivery['source_provenance']}, {'openalex', 'crossref'})
        for provenance, reference in zip(delivery['source_provenance'], delivery['evidence']):
            source = records['source'][provenance['source_id']]
            self.assertEqual(provenance['kind'], 'availability_evidence')
            for key in ('provider', 'requested_identifier', 'observed_identifier', 'http_status',
                        'captured_at', 'capture_method', 'origin_verified', 'response_complete'):
                self.assertEqual(provenance[key], source.get(key))
            self.assertEqual(provenance['original_descriptor']['sha256'], reference['sha256'])
            self.assertEqual(provenance['delivered_sha256'], [reference['sha256']])
            self.assertIn(self.artifacts.read(reference).decode(), [e['content'] for e in packet['evidence']])
        self.assertTrue(delivery['reason'].endswith(
            '`unavailable_qualified` settles this retrieval request with the attached unavailability evidence, '
            'grants no reading or scientific-support credit, and permits you to request full text or return an '
            'assessment that retains the limitation.'))

    def test_qualified_answer_keeps_partial_abstract(self):
        availability = registry_absence(self, partial=True)
        _, packet, delivery = self.qualified_continuation(availability)
        self.assertEqual(delivery['status'], 'unavailable_qualified')
        abstract = self.store.snapshot()['records']['work'][availability['version_id']]['abstracts'][0]
        self.assertIn(abstract['artifact'], delivery['evidence'])
        provenance = next(p for p in delivery['source_provenance'] if p['kind'] == 'abstract')
        self.assertEqual(provenance['completeness'], 'partial')
        self.assertFalse(provenance['origin_verified'])
        self.assertEqual(provenance['delivered_sha256'], [abstract['artifact']['sha256']])
        self.assertIn('A partial publisher abstract snippet.', [e['content'] for e in packet['evidence']])
        self.assertEqual(packet['pending_source_requests'], [])

    def test_qualified_abstract_does_not_attach_fulltext(self):
        from research_harness import review_excerpts
        availability = registry_absence(self)
        version = availability['version_id']
        held = self.capture(version, abstract=False, body='The held exact full text. References: none.')
        self.mutate(record_availability, availability)
        self.assign()
        self.request([{'id': 'abstract', 'kind': 'source', 'version_id': version, 'depth': 'abstract'},
                      {'id': 'body', 'kind': 'source', 'version_id': version, 'depth': 'fulltext'}])
        records = self.store.snapshot()['records']
        from research_harness import bar_sources
        abstract = bar_sources._source(records, self.artifacts,
            {'id': 'abstract', 'kind': 'source', 'version_id': version, 'depth': 'abstract'}, {})
        self.assertEqual(abstract['status'], 'unavailable_qualified')
        self.assertEqual(abstract['fulltext_alternative'], {'version_id': version, 'held': True,
            'source_ids': [held['source_id']], 'request_depth': 'fulltext'})
        references = protocol.collect_references(abstract)
        self.assertNotIn(held['text'], references)
        self.assertNotIn(held['original'], references)
        # A body fitting the exact whole-turn budget must still arrive whole after the abstract answer.
        characters = bar_sources._count_text_characters(self.artifacts, [held['original'], held['text']])
        with patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', characters):
            self.assign('qualified', prior_assignment_id='a')
        packet = self.packet('qualified')
        self.assertEqual([d['status'] for d in packet['source_deliveries']], ['unavailable_qualified', 'delivered'])
        self.assertNotIn('The held exact full text.', json.dumps(abstract))

    def test_qualified_abstract_only_advertises_verified_fulltext(self):
        import copy
        from research_harness import bar_sources
        availability = registry_absence(self)
        version = availability['version_id']
        held = self.capture(version, abstract=False)
        self.mutate(record_availability, availability)
        records = self.store.snapshot()['records']
        request = {'id': 'abstract', 'kind': 'source', 'version_id': version, 'depth': 'abstract'}
        for kind in ('missing_original', 'missing_text', 'corrupt_original', 'corrupt_text', 'wrong_version'):
            with self.subTest(kind=kind):
                candidate = copy.deepcopy(records)
                path = self.root / held['original' if kind.endswith('_original') else 'text']['path']
                original = path.read_bytes()
                if kind.startswith('missing'):
                    path.unlink()
                elif kind.startswith('corrupt'):
                    path.chmod(0o600)
                    path.write_bytes(b'Altered extraction')
                else:
                    candidate['source'][held['source_id']]['requested_identifier'] = 'openalex:W999'
                try:
                    delivery = bar_sources._source(candidate, self.artifacts, request, {})
                    self.assertEqual(delivery['status'], 'unavailable_qualified')
                    self.assertNotIn('fulltext_alternative', delivery)
                    self.assertNotIn(held['text'], protocol.collect_references(delivery))
                    self.assertNotIn(held['original'], protocol.collect_references(delivery))
                finally:
                    path.write_bytes(original)

    def test_qualification_matches_accepted_identifier_spelling(self):
        availability = dict(registry_absence(self), version_id='W123')
        self.mutate(record_availability, availability)
        self.assign()
        request = {'id': 'abstract', 'kind': 'source', 'version_id': 'openalex:W123', 'depth': 'abstract'}
        self.request([request])
        self.assign('qualified', prior_assignment_id='a')
        packet = self.packet('qualified')
        self.assertEqual(packet['source_deliveries'][0]['status'], 'unavailable_qualified')
        self.assertEqual(packet['source_deliveries'][0]['availability']['version_id'], 'W123')
        self.assertEqual(packet['pending_source_requests'], [])

    def test_complete_source_precedes_old_unavailability(self):
        from research_harness.acquisition import acquire_work
        from research_fixtures import client, json_response, openalex
        availability = registry_absence(self)
        self.mutate(record_availability, availability)
        http, _, _ = client([json_response(openalex())])
        acquire_work(self.store, 'W123', http=http, expected_revision=self.store.revision, request_id='complete-later')
        self.assign()
        self.request([{'id': 'abstract', 'kind': 'source', 'version_id': availability['version_id'], 'depth': 'abstract'}])
        self.assign('complete', prior_assignment_id='a')
        delivery = self.packet('complete')['source_deliveries'][0]
        self.assertEqual(delivery['status'], 'delivered')
        self.assertNotIn('availability', delivery)
        self.assertIn('An original abstract.', json.dumps(self.packet('complete')))

    def test_terminal_fulltext_qualification_settles_request(self):
        version = self.metadata()
        availability = terminal_absence(self, version)
        _, packet, delivery = self.qualified_continuation(availability, 'fulltext')
        self.assertEqual(delivery['status'], 'unavailable_qualified')
        self.assertEqual(packet['pending_source_requests'], [])
        self.assertFalse(delivery['reading_credit'])
        self.assertFalse(delivery['scientific_truth_certified'])
        source = self.store.snapshot()['records']['source'][availability['source_ids'][0]]
        self.assertEqual(delivery['evidence'], [source['response']])
        self.assertEqual(delivery['source_provenance'][0]['http_status'], 404)
        self.assertEqual(delivery['source_provenance'][0]['kind'], 'availability_evidence')
        self.assertIn(self.artifacts.read(source['response']).decode(), [e['content'] for e in packet['evidence']])
        self.assertNotIn('fulltext_alternative', delivery)
        self.invoke(self.review_response('bar', status='unresolved'), 'qualified')
        records = self.store.snapshot()['records']
        self.assertEqual(protocol._find_session_bodies(records, self.artifacts,
            records['review_assignment']['qualified']), {})

    def test_binary_qualification_stays_pending_on_text_only_route(self):
        from research_harness.review_transport import text_only_delivery
        version = self.metadata()
        failure = self.capture(version, abstract=False, status=404, pdf_text='Unused extraction')
        availability = {'id': 'binary-terminal', 'profile': 'research', 'version_id': version,
            'depth': 'fulltext', 'source_ids': [failure['source_id']],
            'reason': 'The origin returned a complete binary HTTP 404 response.',
            'policy': {'id': 'terminal-origin', 'minimum_attempts': 1, 'allowed_statuses': [404],
                       'rationale': 'A terminal origin response documents retrieval failure.'}}
        from research_harness import bar_sources
        self.mutate(record_availability, availability)
        self.assign()
        request = {'id': 'qualified', 'kind': 'source', 'version_id': version, 'depth': 'fulltext'}
        output = self.request([request])
        with text_only_delivery(True):
            packet = bar_sources.continuation(self.store.snapshot()['records'], self.artifacts,
                                              self.packet(), output)
        delivery = packet['source_deliveries'][0]
        self.assertEqual(packet['pending_source_requests'], [request])
        self.assertEqual(delivery['status'], 'unavailable')
        self.assertIn('text-only', delivery['reason'])
        self.assertEqual(delivery['evidence'], [])
        self.assertEqual(delivery['source_provenance'], [])
        self.assertNotIn('availability', delivery)
        self.assertFalse(delivery['reading_credit'])
        self.assertFalse(delivery['scientific_truth_certified'])
        with text_only_delivery(True):
            self.assign('qualified', prior_assignment_id='a')
        self.assertEqual(self.packet('qualified')['pending_source_requests'], [request])

    def test_binary_qualification_settles_on_capable_route(self):
        version = self.metadata()
        failure = self.capture(version, abstract=False, status=404, pdf_text='Unused extraction')
        availability = {'id': 'binary-terminal', 'profile': 'research', 'version_id': version,
            'depth': 'fulltext', 'source_ids': [failure['source_id']],
            'reason': 'The origin returned a complete binary HTTP 404 response.',
            'policy': {'id': 'terminal-origin', 'minimum_attempts': 1, 'allowed_statuses': [404],
                       'rationale': 'A terminal origin response documents retrieval failure.'}}
        _, packet, delivery = self.qualified_continuation(availability, 'fulltext')
        self.assertEqual(delivery['status'], 'unavailable_qualified')
        self.assertEqual(packet['pending_source_requests'], [])
        self.assertEqual(delivery['evidence'], [failure['original']])
        self.assertEqual(delivery['source_provenance'][0]['delivered_sha256'],
                         [failure['original']['sha256']])
        self.assertFalse(delivery['reading_credit'])
        self.assertFalse(delivery['scientific_truth_certified'])

    def test_json_qualification_settles_on_text_only_route(self):
        from research_harness.review_transport import text_only_delivery
        availability = registry_absence(self)
        with text_only_delivery(True):
            _, packet, delivery = self.qualified_continuation(availability)
        self.assertEqual(delivery['status'], 'unavailable_qualified')
        self.assertEqual(packet['pending_source_requests'], [])
        records = self.store.snapshot()['records']
        self.assertEqual(delivery['evidence'],
                         [records['source'][s]['response'] for s in availability['source_ids']])
        self.assertTrue(all(p['delivery_mode'] == 'exact-bytes' for p in delivery['source_provenance']))

    def test_terminal_failure_cannot_excuse_required_units(self):
        from research_harness.literature import import_bundle
        version = self.metadata()
        availability = terminal_absence(self, version)
        held = self.capture(version)
        bundle = LiteratureCase.bundle(self, version, held)
        bundle['units'].append({'id': 'figure', 'kind': 'figure', 'required': True, 'link': None,
                                'url': 'https://example.org/required-figure.png',
                                'reason': 'The required figure has not been captured.'})
        self.mutate(import_bundle, bundle)
        request, packet, delivery = self.qualified_continuation(availability, 'fulltext')
        self.assertEqual(delivery['status'], 'partial')
        self.assertEqual(packet['pending_source_requests'], [request])
        self.assertTrue(delivery['source_scope_pending'])
        self.assertNotIn('availability', delivery)

    def test_unqualified_unavailable_stays_pending(self):
        availability = registry_absence(self)
        self.assign()
        request = {'id': 'missing', 'kind': 'source', 'version_id': availability['version_id'], 'depth': 'abstract'}
        self.request([request])
        self.assign('missing', prior_assignment_id='a')
        self.assertEqual(self.packet('missing')['source_deliveries'][0]['status'], 'unavailable')
        self.assertEqual(self.packet('missing')['pending_source_requests'], [request])

    def test_initial_packet_keeps_complete_inventory_access_without_unread_abstracts(self):
        self.cohort(range(1, 1004))
        self.assign()
        packet = self.packet()
        self.assertNotIn('studies bounded sequences', json.dumps(packet))
        inventory = packet['source_inventory']
        self.assertEqual(inventory['total'], 1003)
        self.assertEqual(len(inventory['first_page']['items']), inventory['page_size'])
        descriptor = inventory['artifact_descriptor']
        saved = {'path': descriptor['location'], **{k: descriptor[k] for k in ('sha256', 'size', 'media_type')}}
        rows = json.loads(self.artifacts.read(saved))
        self.assertEqual(len(rows), 1003)
        self.assertEqual(inventory['digest'], digest(rows))
        self.assertEqual([r['id'] for r in rows], sorted(self.store.snapshot()['records']['work']))
        self.assertNotIn(descriptor['sha256'], [e['artifact']['sha256'] for e in packet['evidence']])
        self.assertLess(len(json.dumps(packet)), 15000)

    def test_exact_previously_read_span_does_not_deliver_other_source_bytes(self):
        source = self.metadata(1, abstract='READ EXACT SENTENCE. UNRELATED LONG ABSTRACT TAIL.')
        self.mutate(record_reading, self.abstract_note(source))
        abstract = self.store.snapshot()['records']['work'][source]['abstracts'][0]
        link = self.link(source, abstract['source_id'], abstract['artifact'], 'READ EXACT SENTENCE.')
        self.assign(source_links=[link])
        encoded = json.dumps(self.packet())
        self.assertEqual(encoded.count('READ EXACT SENTENCE.'), 1)
        self.assertNotIn('UNRELATED LONG ABSTRACT TAIL.', encoded)
        self.assertNotIn('Discriminator', encoded)
        self.assertTrue(self.packet()['prior_context'][0]['inspection']['mechanical_only'])
        self.assertEqual(self.packet()['prior_context'][0]['origin']['artifact_descriptor']['sha256'], abstract['artifact']['sha256'])

    def test_unread_initial_source_link_is_rejected(self):
        source = self.metadata(1)
        abstract = self.store.snapshot()['records']['work'][source]['abstracts'][0]
        self.error('reading_missing', lambda: self.assign(source_links=[self.link(source, abstract['source_id'], abstract['artifact'])]))

    def test_requested_source_is_delivered_with_same_session_and_full_history(self):
        source = self.metadata(1, abstract='UNREAD SOURCE REQUESTED BY THE REVIEWER.')
        self.assign()
        response = self.request([{'id': 'source-1', 'kind': 'source', 'version_id': source, 'depth': 'abstract'}])
        self.error('review_sources_pending', lambda: self.mutate(research_decisions.record_value_review,
            dict(response, id='bar-a', dossier_id='dossier-1', assignment_id='a')))
        continued = self.assign('b', prior_assignment_id='a')
        self.assertEqual(continued['session_id'], 'a')
        self.assertEqual(len(read_history(self.artifacts, continued)), 2)
        self.assertIn('UNREAD SOURCE REQUESTED BY THE REVIEWER.', json.dumps(self.packet('b')))
        delivery = self.packet('b')['source_deliveries'][0]
        self.assertEqual(delivery['status'], 'delivered')
        self.assertEqual(delivery['request'], response['source_requests'][0])
        self.invoke(self.review_response('bar'), 'b')
        self.mutate(research_decisions.record_value_review, dict(self.review_response('bar'), id='bar-a', dossier_id='dossier-1', assignment_id='b'))
        self.error('review_resampling_forbidden', lambda: self.assign('c', prior_assignment_id='b'))
        self.error('review_resampling_forbidden', lambda: self.assign('d'))
        self.assign('other', reviewer='reviewer-b')
        self.error('review_slots_fixed', lambda: self.assign('third', reviewer='reviewer-c'))

    def test_inventory_pages_and_queries_come_from_complete_immutable_snapshot(self):
        self.cohort(range(1, 114))
        self.assign()
        self.request([{'id': 'page', 'kind': 'inventory_page', 'page': 11},
                      {'id': 'query', 'kind': 'inventory_query', 'query': 'arxiv:2601.00113v1', 'page': 0}])
        self.metadata(999)
        self.assign('b', prior_assignment_id='a')
        deliveries = self.packet('b')['source_deliveries']
        self.assertEqual(deliveries[0]['page']['total'], 113)
        self.assertEqual(len(deliveries[0]['page']['items']), 3)
        self.assertIsNone(deliveries[0]['page']['next_page'])
        self.assertEqual(deliveries[1]['page']['items'][0]['id'], 'arxiv:2601.00113v1')
        self.assertEqual(self.packet('b')['source_inventory']['digest'], self.packet()['source_inventory']['digest'])

    def test_unavailable_source_request_cannot_disappear_in_an_inventory_continuation(self):
        source = self.metadata(1)
        self.assign()
        request = {'id': 'body', 'kind': 'source', 'version_id': source, 'depth': 'fulltext'}
        self.request([request])
        self.assign('b', prior_assignment_id='a')
        self.assertEqual(self.packet('b')['source_deliveries'][0]['status'], 'unavailable')
        self.request([{'id': 'page', 'kind': 'inventory_page', 'page': 0}], 'b')
        self.assign('c', prior_assignment_id='b')
        self.invoke(self.review_response('bar'), 'c')
        self.error('review_sources_pending', lambda: self.mutate(research_decisions.record_value_review,
            dict(self.review_response('bar'), id='bar-a', dossier_id='dossier-1', assignment_id='c')))
        self.assertEqual(self.packet('c')['pending_source_requests'], [request])

    def test_pending_chain_cannot_fork_or_change_reviewer(self):
        self.metadata(1)
        self.assign()
        self.request([{'id': 'page', 'kind': 'inventory_page', 'page': 0}])
        self.error('review_source_continuation_mismatch', lambda: self.assign('wrong', reviewer='reviewer-b', prior_assignment_id='a'))
        self.assign('b', prior_assignment_id='a')
        self.error('review_source_continuation_fork', lambda: self.assign('fork', prior_assignment_id='a'))

    def test_boolean_page_cannot_continue(self):
        self.metadata(1)
        self.assign()
        self.request([{'id': 'page', 'kind': 'inventory_page', 'page': True}])
        self.error('invalid_review_source_request', lambda: self.assign('b', prior_assignment_id='a'))

    def test_source_hash_corruption_cannot_be_delivered(self):
        source = self.metadata(1)
        self.assign()
        self.request([{'id': 'abstract', 'kind': 'source', 'version_id': source, 'depth': 'abstract'}])
        abstract = self.store.snapshot()['records']['work'][source]['abstract']
        (self.root / abstract['path']).chmod(0o600)
        (self.root / abstract['path']).write_bytes(b'corrupted source')
        with self.assertRaises(ResearchError):
            self.assign('b', prior_assignment_id='a')

    def test_sufficient_response_with_source_requests_cannot_continue_or_finalize(self):
        self.metadata(1)
        self.assign()
        response = self.review_response('bar')
        response['source_requests'] = [{'id': 'page', 'kind': 'inventory_page', 'page': 0}]
        self.invoke(response)
        self.error('invalid_review_source_request', lambda: self.assign('b', prior_assignment_id='a'))
        refusal = self.assert_refusal('invalid_review_source_request', lambda: self.mutate(
            research_decisions.record_value_review,
            dict(response, id='bar-a', dossier_id='dossier-1', assignment_id='a')), 'attempt-a')
        self.assertEqual(refusal['check'], 'review.source_requests')

    def test_out_of_range_page_is_explicitly_rejected(self):
        self.metadata(1)
        self.assign()
        self.request([{'id': 'page', 'kind': 'inventory_page', 'page': 1}])
        self.error('invalid_review_source_request', lambda: self.assign('b', prior_assignment_id='a'))

    def test_later_native_acquisition_resolves_the_inherited_request(self):
        source = self.metadata(1)
        self.assign()
        request = {'id': 'body', 'kind': 'source', 'version_id': source, 'depth': 'fulltext'}
        self.request([request])
        self.assign('b', prior_assignment_id='a')
        self.request([], 'b')
        self.capture(source, body='THE NEWLY ACQUIRED FULL BODY. References: none.')
        self.assign('c', prior_assignment_id='b')
        packet = self.packet('c')
        self.assertEqual(packet['pending_source_requests'], [])
        self.assertEqual(packet['source_inventory'], self.packet()['source_inventory'])
        self.assertIn('THE NEWLY ACQUIRED FULL BODY.', json.dumps(packet))
        self.assertTrue(packet['source_deliveries'][0]['native_source_delta']['changed'])
        response = dict(self.review_response('bar'), source_requests=[])
        self.invoke(response, 'c')
        self.mutate(research_decisions.record_value_review,
            dict(response, id='bar-a', dossier_id='dossier-1', assignment_id='c'))
        self.assertEqual(len(read_history(self.artifacts, self.store.snapshot()['records']['review_assignment']['c'])), 4)

    def test_tampered_reading_is_inventory_metadata_without_current_read_credit(self):
        source = self.metadata(1)
        self.mutate(record_reading, self.abstract_note(source))
        abstract = self.store.snapshot()['records']['work'][source]['abstract']
        (self.root / abstract['path']).chmod(0o600)
        (self.root / abstract['path']).write_bytes(b'corrupt')
        self.assign()
        row = self.packet()['source_inventory']['first_page']['items'][0]
        self.assertEqual(row['recorded_reading_status'], ['abstract'])
        self.assertEqual(row['current_reading_status'], [])
        self.assertEqual(row['unverified_or_partial_reading_status'], ['abstract'])

    def test_partial_mcp_abstract_remains_pending_with_its_actual_origin(self):
        from research_harness.acquisition import import_response
        raw = {'results': [{'identifier': 'https://example.org/notebook', 'name': 'Authored notebook',
                            'summary': 'A partial abstract snippet.'}]}
        import_response(self.store, 'mcp', json.dumps(raw).encode(), source_url='https://example.org/search',
            captured_at='2026-09-07T00:00:00Z', request_id='partial-abstract', expected_revision=self.store.revision,
            mappings=[{'id': '/results/0/identifier', 'title': '/results/0/name', 'abstract': '/results/0/summary'}],
            media_type='application/json')
        self.assign()
        request = {'id': 'abstract', 'kind': 'source', 'version_id': 'url:https://example.org/notebook', 'depth': 'abstract'}
        self.request([request])
        self.assign('b', prior_assignment_id='a')
        packet = self.packet('b')
        self.assertEqual(packet['source_deliveries'][0]['status'], 'partial')
        self.assertEqual(packet['pending_source_requests'], [request])
        self.assertIn('A partial abstract snippet.', json.dumps(packet))
        provenance = packet['source_deliveries'][0]['source_provenance'][0]
        self.assertEqual(provenance['completeness'], 'partial')
        self.assertFalse(provenance['origin_verified'])
        self.assertEqual(provenance['capture_method'], 'external_import')

    def visual_source(self, acquire=True):
        from research_fixtures import client
        from research_harness.visual_assets import acquire_visual_asset
        source = self.metadata(1)
        capture = self.capture(source, body='<figure><img src="figure.png"><figcaption>Scientific figure.</figcaption></figure> References: none.')
        link = {'version_id': source, 'source_id': capture['source_id'], 'artifact': capture['original'],
                'locator': {'kind': 'html', 'anchor': self.span(capture['original'], '<figcaption>Scientific figure.</figcaption>')}}
        result = None
        if acquire:
            # The fixture is generated as a complete valid PNG with checked CRCs.
            import struct, zlib
            def chunk(kind, data):
                return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
            png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00')) + chunk(b'IEND', b'')
            http, _, _ = client([(200, {'Content-Type': 'image/png'}, png)])
            result = acquire_visual_asset(self.store, link, 'https://arxiv.org/html/figure.png',
                request_id='figure', expected_revision=self.store.revision, http=http)
        return source, result

    def test_fulltext_request_delivers_the_current_held_original_figure(self):
        source, result = self.visual_source()
        self.assign()
        self.request([{'id': 'body', 'kind': 'source', 'version_id': source, 'depth': 'fulltext'}])
        self.assign('b', prior_assignment_id='a')
        delivery = self.packet('b')['source_deliveries'][0]
        self.assertEqual(delivery['status'], 'delivered')
        self.assertIn(result['asset_link']['artifact']['sha256'], [ref['sha256'] for ref in delivery['evidence']])
        self.assertTrue(any(p.get('kind') == 'visual_asset' for p in delivery['source_provenance']))
        self.assertEqual(delivery['source_scope_pending'], [])
        self.assertFalse(delivery['reading_credit'])

    def test_fulltext_request_keeps_uncaptured_figure_scope_pending(self):
        source, _ = self.visual_source(acquire=False)
        self.assign()
        request = {'id': 'body', 'kind': 'source', 'version_id': source, 'depth': 'fulltext'}
        self.request([request])
        self.assign('b', prior_assignment_id='a')
        delivery = self.packet('b')['source_deliveries'][0]
        self.assertEqual(delivery['status'], 'partial')
        self.assertIn('visual_asset_missing', [p['code'] for p in delivery['source_scope_pending']])
        self.assertEqual(self.packet('b')['pending_source_requests'], [request])

    def test_continuation_uses_the_original_inventory_page_size(self):
        from research_harness import bar_sources
        self.cohort(range(1, 114))
        with patch.object(bar_sources, 'PAGE_SIZE', 40):
            self.assign()
        self.request([{'id': 'page', 'kind': 'inventory_page', 'page': 2}])
        self.assign('b', prior_assignment_id='a')
        page = self.packet('b')['source_deliveries'][0]['page']
        self.assertEqual(page['page_size'], 40)
        self.assertEqual(len(page['items']), 33)
        self.assertEqual(page['items'][0]['id'], 'arxiv:2601.00081v1')

    def test_versionless_source_request_is_not_an_exact_native_version(self):
        self.metadata(1)
        self.assign()
        self.request([{'id': 'source', 'kind': 'source', 'version_id': 'arxiv:2601.00001', 'depth': 'abstract'}])
        self.error('invalid_review_source_request', lambda: self.assign('b', prior_assignment_id='a'))

    def unit_source(self, mode):
        from research_harness.literature import import_bundle
        source = self.metadata(1)
        body = {'table': '<table><tr><td><img src="table.png"></td></tr></table> References: none.',
                'equation': '<div class="ltx_equation"><canvas>Formula surface</canvas></div> References: none.'}.get(
                    mode, 'The article requires the supplementary derivation. References: none.')
        capture = self.capture(source, body=body)
        bundle = LiteratureCase.bundle(self, source, capture, complete=mode != 'partial')
        if mode == 'missing':
            bundle['units'].append({'id': 'si', 'kind': 'supplement', 'required': True, 'link': None,
                'reason': 'The required derivation is not captured.', 'url': 'https://example.org/si.pdf'})
        if mode in ('table', 'equation'):
            quote = '<img src="table.png">' if mode == 'table' else 'Formula surface'
            bundle['units'].append({'id': 'visual', 'kind': mode, 'required': True, 'link': {
                'version_id': source, 'source_id': capture['source_id'], 'artifact': capture['original'],
                'locator': {'kind': 'html', 'anchor': self.span(capture['original'], quote)}}})
        self.mutate(import_bundle, bundle)
        return source, bundle

    def unit_request(self, source):
        self.assign()
        self.request([{'id': 'body', 'kind': 'source', 'version_id': source, 'depth': 'fulltext'}])
        self.assign('b', prior_assignment_id='a')
        self.assertTrue(self.packet('b')['pending_source_requests'])
        return self.packet('b')['source_deliveries'][0]

    def test_required_supplement_remains_pending_in_fulltext_delivery(self):
        source, bundle = self.unit_source('missing')
        delivery = self.unit_request(source)
        self.assertEqual(delivery['status'], 'partial')
        self.assertIn('required_unit_missing', [p['code'] for p in delivery['source_scope_pending']])
        self.assertEqual(delivery['declared_bundles'][0]['id'], bundle['id'])
        self.assertIsNone(delivery['declared_bundles'][0]['units'][-1]['link'])

    def test_declared_partial_article_bundle_is_not_delivered_as_complete(self):
        source, _ = self.unit_source('partial')
        delivery = self.unit_request(source)
        self.assertEqual(delivery['status'], 'partial')
        self.assertEqual(delivery['declared_bundles'][0]['completeness'], 'partial')
        self.assertIn('source_bundle_incomplete', [p['code'] for p in delivery['source_scope_pending']])

    def test_table_original_dependencies_remain_pending(self):
        source, _ = self.unit_source('table')
        delivery = self.unit_request(source)
        self.assertEqual(delivery['status'], 'partial')
        self.assertIn('visual_asset_missing', [p['code'] for p in delivery['source_scope_pending']])

    def test_dynamic_equation_scope_remains_pending(self):
        source, _ = self.unit_source('equation')
        delivery = self.unit_request(source)
        self.assertEqual(delivery['status'], 'partial')
        self.assertIn('dynamic_visual_unsupported', [p['code'] for p in delivery['source_scope_pending']])

    def test_bundle_only_required_unit_change_is_a_declared_native_delta(self):
        from research_harness.literature import import_bundle
        source, bundle = self.unit_source('complete')
        self.assign()
        self.request([{'id': 'body', 'kind': 'source', 'version_id': source, 'depth': 'fulltext'}])
        bundle['id'] = 'extended'
        bundle['units'].append({'id': 'si', 'kind': 'supplement', 'required': True, 'link': None,
            'reason': 'A required derivation was identified.', 'url': 'https://example.org/si.pdf'})
        self.mutate(import_bundle, bundle)
        self.assign('b', prior_assignment_id='a')
        delivery = self.packet('b')['source_deliveries'][0]
        self.assertTrue(delivery['native_source_delta']['changed'])
        self.assertEqual(delivery['status'], 'partial')
        self.assertEqual(self.packet('b')['source_inventory'], self.packet()['source_inventory'])
