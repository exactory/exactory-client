"""A recorded choice changes future source delivery while retaining exact earlier evidence."""

import copy
import hashlib
import unittest

from codex_route_fixtures import record_codex_route
from literature_fixtures import FIELDS, LiteratureCase
from strategy_fixtures import StrategyCase
from test_reviewer_excerpt_sessions import ExcerptSessionCase
from research_harness import bar_sources, cli, graph, reading, review_excerpts
from research_harness import review_protocol as protocol, review_supplied_evidence
from research_harness.acquisition import acquire_fulltext
from research_harness.capture_selection import (PACKET_KEY, PROTOCOL, bind_packet_selections,
                                               select_main_capture)
from research_harness.literature import import_bundle
from research_harness.source_links import span_locator
from research_fixtures import client


class MainCaptureSelectionTests(LiteratureCase):
    def setUp(self):
        super().setUp()
        self.version = self.metadata()
        self.first = self.capture(self.version, pdf_text='The original layout theorem. References: none.\f')
        self.compact = self.capture(self.version, pdf_text='The compact reading order theorem. References: none.\f')
        self.payload = dict(version_id=self.version, source_id=self.compact['source_id'], reason='Keep the compact extraction.')

    def test_selection_retains_capture_bytes_and_digests_and_replays_after_another_choice(self):
        before = self.store.snapshot()['records']['work'][self.version]
        result = self.mutate(select_main_capture, self.payload)
        selected = self.store.snapshot()['records']
        self.assertEqual(graph.main_captures(selected, before), [self.compact])
        self.assertEqual(graph.main_captures(selected, before, {'id': self.version, 'sha256': self.first['original']['sha256']}),
                         [self.compact])
        self.assertEqual(selected['work'][self.version], before)
        choice = result['result']
        self.assertEqual(choice, dict(self.payload, original_sha256=self.first['original']['sha256'],
                                     text_sha256=self.compact['text']['sha256'], protocol=PROTOCOL))
        request = 'task3-' + str(self.sequence)
        self.mutate(select_main_capture, dict(self.payload, source_id=self.first['source_id']))
        self.assertEqual(select_main_capture(self.store, self.payload, expected_revision=0, request_id=request), result)
        self.assertEqual(graph.main_captures(self.store.snapshot()['records'], before), [self.first])
        for capture in before['fulltexts']:
            self.assertEqual(hashlib.sha256(self.artifacts.read(capture['text'])).hexdigest(), capture['text']['sha256'])

    def test_choice_is_scoped_to_one_original_and_one_exact_version(self):
        other_version = self.metadata(2)
        other = self.capture(other_version, pdf_text='A different exact version of the same fixture PDF.\f')
        html = self.capture(self.version, abstract=False)
        self.mutate(select_main_capture, self.payload)
        records = self.store.snapshot()['records']
        self.assertEqual(graph.main_captures(records, records['work'][self.version]), [self.compact, html])
        self.assertEqual(graph.main_captures(records, records['work'][other_version]), [other])
        self.assertEqual(graph.main_captures(records, records['work'][self.version],
                         {'id': self.version, 'sha256': html['original']['sha256']}), [html])

    def test_recapture_preserves_original_order_when_another_main_was_captured_between_them(self):
        version = self.metadata(3)
        first = self.capture(version, pdf_text='The first PDF layout text.\f')
        html = self.capture(version, abstract=False)
        selected = self.capture(version, pdf_text='The chosen compact PDF text.\f')
        self.mutate(select_main_capture, dict(version_id=version, source_id=selected['source_id'], reason='Keep compact PDF text.'))
        records = self.store.snapshot()['records']
        self.assertEqual(graph.main_captures(records, records['work'][version]), [selected, html])
        self.assertEqual(review_excerpts.find_fulltext(records, version), selected['text'])
        self.assertEqual(records['work'][version]['fulltexts'][0], first)

    def test_unknown_wrong_version_and_pending_captures_are_refused_without_writes(self):
        other = self.metadata(2)
        http, _, _ = client([(404, {'Content-Type': 'application/pdf'}, b'Absent')], max_retries=0)
        pending = acquire_fulltext(self.store, self.version, 'https://arxiv.org/pdf/' + self.version[6:], http=http,
                                  expected_revision=self.store.revision, request_id='pending')['capture']
        before = self.store.snapshot()
        for value, code in ((dict(self.payload, source_id='missing'), 'invalid_main_capture'),
                            (dict(self.payload, version_id=other), 'invalid_main_capture'),
                            (dict(self.payload, source_id=pending['source_id']), 'invalid_main_capture'),
                            (dict(self.payload, version_id='arxiv:2601.99999v1'), 'unknown_work'),
                            (dict(self.payload, version_id='arxiv:2601.00001'), 'unknown_work'),
                            (dict(self.payload, reason=''), 'invalid_input'),
                            (dict(self.payload, source_id=[]), 'invalid_input')):
            with self.subTest(value=value):
                self.assert_error(code, lambda: self.mutate(select_main_capture, value))
        self.assertEqual(self.store.snapshot(), before)

    def test_component_and_inventoried_supplement_cannot_become_main(self):
        records = self.store.snapshot()['records']
        for change in ('component', 'supplement'):
            altered = copy.deepcopy(records)
            if change == 'component':
                altered['work'][self.version]['fulltexts'][1]['component'] = {'status': 'bound'}
            else:
                bundle = self.bundle(self.version, self.first)
                bundle['units'].append({'id': 'supplement', 'kind': 'supplement',
                                        'link': self.link(self.version, self.compact['source_id'], self.compact['text'])})
                bundle.update(sequence=1, original_sha256=self.first['original']['sha256'])
                altered['source_bundle'] = {'fixture': bundle}
            def install(transaction):
                transaction.put('work', self.version, altered['work'][self.version])
                for key, value in altered.get('source_bundle', {}).items():
                    transaction.put('source_bundle', key, value)
            self.store.mutate('fixture.install', {}, install, expected_revision=self.store.revision,
                              request_id='install-' + change)
            before = self.store.snapshot()
            with self.subTest(change=change):
                self.assert_error('invalid_main_capture', lambda: self.mutate(select_main_capture, self.payload))
            self.assertEqual(self.store.snapshot(), before)

    def test_missing_or_corrupt_selected_artifacts_are_refused_without_recording_a_choice(self):
        for field in ('original', 'text'):
            reference = self.compact[field]
            path = self.root / reference['path']
            data = path.read_bytes()
            before = self.store.snapshot()
            for missing, code in ((True, 'artifact_missing'), (False, 'artifact_corrupt')):
                with self.subTest(field=field, missing=missing):
                    path.chmod(0o600)
                    if missing:
                        path.unlink()
                    else:
                        path.write_bytes(b'Altered selected capture')
                    self.assert_error(code, lambda: self.mutate(select_main_capture, self.payload))
                    self.assertEqual(self.store.snapshot(), before)
                    path.write_bytes(data)

    def test_readings_of_either_capture_keep_their_coverage_and_the_old_bundle_stays_complete(self):
        bundle = self.bundle(self.version, self.first)
        self.mutate(import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle))
        links = []
        for index, capture in enumerate((self.first, self.compact)):
            link = self.link(self.version, capture['source_id'], capture['text'])
            links.append(link)
            self.mutate(reading.record_reading, dict(id='passage-' + str(index), version_id=self.version, depth='passage',
                inspections=[dict(unit_id=None, link=link, note='Inspect this extraction.')],
                notes={field: dict(text='The passage describes ' + field, status='present', inspections=[0]) for field in FIELDS}))
        records = self.store.snapshot()['records']
        before = reading.fulltext_coverage(records, self.artifacts, self.version)
        self.assertTrue(before['complete'])
        readings = copy.deepcopy(records['reading'])
        self.mutate(select_main_capture, self.payload)
        records = self.store.snapshot()['records']
        self.assertEqual(records['reading'], readings)
        self.assertEqual(reading.fulltext_coverage(records, self.artifacts, self.version), before)
        for link in links:
            self.assertEqual(reading.validate_read_evidence(records, self.artifacts, link, depth='passage')['depth'], 'passage')

    def test_cli_registers_the_command_and_returns_its_example(self):
        self.assertIs(cli.OPERATIONS['select-main-capture'], select_main_capture)
        example = cli.run(cli.build_parser().parse_args(['example', 'select-main-capture']))
        self.assertEqual(set(example), {'version_id', 'source_id', 'reason'})
        self.assertTrue(example['reason'])

    def test_check_prepares_a_valid_choice_without_changing_the_record(self):
        from research_harness.check_mode import check_mutation
        before = self.store.snapshot()
        files = {str(path.relative_to(self.root)): path.read_bytes()
                 for path in self.root.rglob('*') if path.is_file()}
        checked = check_mutation(self.store, select_main_capture, self.payload,
            expected_revision=self.store.revision, request_id='check-choice', bounded_payload=self.payload)
        self.assertEqual(checked['check'], 'accepted')
        self.assertFalse(checked['recorded'])
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual({str(path.relative_to(self.root)): path.read_bytes()
                          for path in self.root.rglob('*') if path.is_file()}, files)
        self.assertEqual(graph.main_captures(before['records'], before['records']['work'][self.version]), [self.first])

    def test_main_choice_retains_component_text_from_the_same_original(self):
        from test_research_components import ComponentTests
        owner = ComponentTests()
        self.addCleanup(owner.doCleanups)
        owner.setUp()
        component = owner.acquire()['capture']
        owner.mutate(import_bundle, owner.filled_bundle(component))
        url = 'https://arxiv.org/pdf/' + owner.identifier[6:]
        old = owner.acquire(component=False, url=url, text=owner.si_text.replace(' ', '   '))['capture']
        selected = owner.acquire(component=False, url=url, text=owner.si_text.replace(' ', '\n'))['capture']
        self.assertEqual(component['original'], selected['original'])
        self.assertNotEqual(component['text'], selected['text'])
        owner.mutate(select_main_capture, dict(version_id=owner.identifier, source_id=selected['source_id'],
                                               reason='Select the new main extraction.'))
        delivery = bar_sources._source(owner.store.snapshot()['records'], owner.artifacts,
            dict(id='body', kind='source', version_id=owner.identifier, depth='fulltext'), {})
        self.assertIn(selected['text'], delivery['evidence'])
        self.assertIn(component['text'], delivery['evidence'])
        self.assertNotIn(old['text'], delivery['evidence'])

    def test_main_choice_retains_inventoried_supplement_from_the_same_original(self):
        supplement = self.capture(self.version, pdf_text='Supplementary theorem. References: none.\f')
        bundle = self.bundle(self.version, self.first)
        bundle['units'].append({'id': 'supplement', 'kind': 'supplement', 'required': True,
                                'link': self.link(self.version, supplement['source_id'], supplement['text'])})
        self.mutate(import_bundle, bundle)
        self.mutate(select_main_capture, self.payload)
        delivery = bar_sources._source(self.store.snapshot()['records'], self.artifacts,
            dict(id='body', kind='source', version_id=self.version, depth='fulltext'), {})
        self.assertIn(self.compact['text'], delivery['evidence'])
        self.assertIn(supplement['text'], delivery['evidence'])
        self.assertNotIn(self.first['text'], delivery['evidence'])


class MainCaptureChainTests(ExcerptSessionCase):
    def setUp(self):
        StrategyCase.setUp(self)
        self.sequence = 0
        self.setup_dossier()
        record_codex_route(self)
        self.version = self.metadata()
        self.old_text = 'Only the layout capture contains this exact first theorem.\n\n' + 'x ' * 180000 + 'References: none.\f'
        self.first = self.capture(self.version, pdf_text=self.old_text)
        self.versions = [self.version]
        self.requests = [dict(id='requested', kind='source', version_id=self.version, depth='fulltext')]

    def test_pending_request_switches_next_turn_and_earlier_parts_excerpts_and_requests_stay_verifiable(self):
        self.assign('a')
        self.invoke('a', self.build_request_turn(self.requests))
        self.assign('b', prior_assignment_id='a')
        earlier = self.packet('b')['source_deliveries'][0]
        self.assertEqual(earlier['part']['part'], 1)
        self.assertEqual(earlier['origin']['artifact_descriptor']['sha256'], self.first['text']['sha256'])
        old_excerpt = dict(version_id=self.version, text=self.old_text[:56], purpose='Keep the original theorem.')
        self.invoke('b', self.build_request_turn(self.requests, [old_excerpt]))
        frozen = self.store.snapshot()['records']
        bindings = {identifier: copy.deepcopy(frozen['review_assignment'][identifier]) for identifier in ('a', 'b')}
        attempts = copy.deepcopy(frozen['review_attempt'])
        packets = {identifier: self.artifacts.read(bindings[identifier]['packet']) for identifier in bindings}
        compact_text = 'The compact capture contains a newly ordered theorem and its proof. References: none.\f'
        compact = self.capture(self.version, pdf_text=compact_text)
        payload = dict(version_id=self.version, source_id=compact['source_id'], reason='Replace the padded delivery.')
        self.mutate(select_main_capture, payload)
        self.assign('c', prior_assignment_id='b')
        packet = self.packet('c')
        self.assertEqual(packet[PACKET_KEY]['protocol'], PROTOCOL)
        self.assertEqual(packet['pending_source_requests'], [])
        self.assertEqual([ref for row in packet['source_deliveries'] for ref in row['evidence']
                          if ref['media_type'].startswith('text/plain')], [compact['text']])
        [excerpt] = packet[review_excerpts.KEY]['excerpts']
        self.assertEqual(excerpt['status'], 'verified')
        self.assertEqual(excerpt['artifact_descriptor']['sha256'], self.first['text']['sha256'])
        self.assertEqual(excerpt['text'], old_excerpt['text'])
        self.invoke('c', self.build_request_turn(self.requests, [dict(version_id=self.version,
            text=compact_text[:65], purpose='Keep the compact theorem.')]))
        self.assign('d', prior_assignment_id='c')
        self.assertEqual(self.packet('d')['source_deliveries'][0]['status'], 'already_delivered')
        self.assertEqual({row['artifact_descriptor']['sha256'] for row in self.packet('d')[review_excerpts.KEY]['excerpts']},
                         {self.first['text']['sha256'], compact['text']['sha256']})
        records = self.store.snapshot()['records']
        for identifier in bindings:
            self.assertEqual(records['review_assignment'][identifier], bindings[identifier])
            self.assertEqual(self.artifacts.read(bindings[identifier]['packet']), packets[identifier])
            attempt = next(row for row in attempts.values() if row['assignment_id'] == identifier)
            self.assertEqual(records['review_attempt'][attempt['id']], attempt)
            self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, bindings[identifier], attempt))
        references, mappings = review_supplied_evidence.supplied_context(records, self.artifacts, ['c'], frozen=True)
        link = dict(version_id=self.version, source_id=self.first['source_id'], artifact=self.first['text'],
                    locator=span_locator(self.old_text, 5, 55))
        self.assertTrue(review_supplied_evidence.is_received(records, dict(kind='source', link=link),
                                                            references, mappings, artifacts=self.artifacts))
        link['locator'] = span_locator(self.old_text, 250000, 250050)
        self.assertFalse(review_supplied_evidence.is_received(records, dict(kind='source', link=link),
                                                             references, mappings, artifacts=self.artifacts))
        start, end = earlier['part']['start'], earlier['part']['end']
        expected = hashlib.sha256(self.old_text[start:end].encode()).hexdigest()
        self.assertEqual(earlier['origin']['locator']['sha256'], expected)
        self.assertEqual(self.artifacts.read(earlier['excerpt']).decode(), self.old_text[start:end])
        self.assertIn(expected, protocol._find_session_bodies(records, self.artifacts, records['review_assignment']['c']))
        self.invoke('d', self.build_request_turn(self.requests))
        self.mutate(select_main_capture, dict(payload, source_id=self.first['source_id']))
        self.assign('e', prior_assignment_id='d')
        resumed = self.packet('e')['source_deliveries'][0]
        self.assertEqual(resumed['part']['part'], 2)
        self.assertEqual(resumed['part']['start'], end)

    def test_selected_capture_filters_old_text_even_when_a_bundle_still_names_it(self):
        # Source-unit links keep their immutable descriptors and reading coverage.
        self.bundle = LiteratureCase.bundle.__get__(self)
        self.link = LiteratureCase.link.__get__(self)
        self.span = LiteratureCase.span.__get__(self)
        bundle = self.bundle(self.version, self.first)
        self.mutate(import_bundle, bundle)
        compact = self.capture(self.version, pdf_text='A short complete extraction. References: none.\f')
        self.mutate(select_main_capture, dict(version_id=self.version, source_id=compact['source_id'], reason='Keep plain text.'))
        records = self.store.snapshot()['records']
        delivery = bar_sources._source(records, self.artifacts, self.requests[0], {})
        self.assertIn(compact['text'], delivery['evidence'])
        self.assertNotIn(self.first['text'], delivery['evidence'])
        self.assertEqual(delivery['declared_bundles'][0]['units'][0]['link']['artifact_descriptor']['sha256'],
                         self.first['text']['sha256'])
        legacy = bind_packet_selections(records, {})
        self.assertEqual(graph.main_captures(legacy, legacy['work'][self.version]), [self.first])


class StoredCompleteCaptureTests(unittest.TestCase):
    def test_frozen_complete_source_manifest_authenticates_after_a_new_main_choice(self):
        from test_research_complete_source_continuation import NativeCompleteSourceTests
        from research_harness import review_source_complete as complete
        case = NativeCompleteSourceTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        assignment = case.assign('complete', source_delivery=complete.MODE)
        case.invoke('complete', case.output([case.second_request]))
        records = case.store.snapshot()['records']
        attempt = records['review_attempt']['attempt-complete']
        frozen_packet = case.artifacts.read(assignment['packet'])
        frozen_request = case.artifacts.read(attempt['request'])
        self.assertTrue(protocol.authenticate_attempt(records, case.artifacts, assignment, attempt))
        compact = case.fixture.capture(case.version, pdf_text='A complete compact PDF theorem and its proof.\f')
        case.fixture.mutate(select_main_capture, dict(version_id=case.version, source_id=compact['source_id'],
                                                    reason='Select the compact PDF extraction.'))
        records = case.store.snapshot()['records']
        self.assertTrue(protocol.authenticate_attempt(records, case.artifacts, assignment, attempt))
        self.assertEqual(case.artifacts.read(assignment['packet']), frozen_packet)
        self.assertEqual(case.artifacts.read(attempt['request']), frozen_request)
        successor = case.assign('selected-next', prior='complete')
        packet = case.packet(successor)
        self.assertEqual(packet[PACKET_KEY]['protocol'], PROTOCOL)
        self.assertEqual(packet[complete.MARKER]['sources'][0]['selected_text_sha256'], compact['text']['sha256'])
        self.assertNotIn(case.pdf['text'], [ref for row in packet['source_deliveries'] for ref in row['evidence']])
