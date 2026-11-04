"""V5 authenticates components within one chain while retaining the original scientific evidence."""

import copy
import hashlib
import json
from unittest.mock import patch

from openness_reading_fixtures import OpennessReadingCase
from openness_search_fixtures import build_fifteen_searches
from research_harness import openness_reading as reading, review_protocol as protocol
from research_harness import review_excerpts, review_transport as transport
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.literature import record_search
from research_harness.problem_openness import assess_openness, serialize_record
import test_research_problem_openness_minimum as minimum_fixtures

V5 = 'exactory-openness-reading-v5'


class SearchSharingCase(OpennessReadingCase):
    text_sizes = (1000,)
    reading_protocol = None

    def record_searches(self):
        first = minimum_fixtures.ProblemOpennessMinimumTests.search(self, self.versions)
        keys = ('id', 'profile', 'purpose', 'question', 'scope', 'queries', 'responses', 'captured_at',
                'found_work_ids', 'verdict', 'cited_work_ids', 'dispositions', 'resolved', 'impact', 'gaps')
        searches = [first]
        for number in range(2):
            value = copy.deepcopy({key: first[key] for key in keys})
            value.update(id='shared-search-%d' % number, purpose=('theory', 'adjacent')[number],
                         impact='Distinct formulation-specific impact %d.' % number)
            value['dispositions'][0]['reason'] = 'Shared judgment λ with exact "quoted" evidence. ' * 5800
            self.mutate(record_search, value)
            searches.append(self.store.snapshot()['records']['literature_search'][value['id']])
        return searches

    def start(self):
        native = self.export(priority=self.versions)
        self.assign('turn-1', native)
        packet = self.read_packet('turn-1')
        self.assertEqual(reading.find_protocol(packet), V5)
        return native

    def complete(self, native=None, respond=None):
        native = native or self.start()
        turns = ['turn-1']
        while reading.find_stage(self.read_packet(turns[-1])) == 'reading':
            packet = self.read_packet(turns[-1])
            output = respond(packet, len(turns)) if respond else self.build_reading_response(packet, excerpts=[])
            self.invoke(turns[-1], output)
            turns.append('turn-%d' % (len(turns) + 1))
            self.assign(turns[-1], native, prior=turns[-2])
            self.assertLess(len(turns), 12)
        return turns

    def record_openness(self, identifier):
        return self.mutate(self.load_problem_module().record_openness, {'assignment_id': identifier})['result']

    def verify(self, identifier):
        records = self.store.snapshot()['records']
        return reading.verify_chain(records, self.artifacts, records['review_assignment'][identifier])


class FifteenSearchChainTests(OpennessReadingCase):
    """The measured delivery shape; scientific search integrity is exercised by SearchSharingTests separately."""
    text_sizes = (1000,)
    reading_protocol = None

    def build_statement(self, identifier='statement-1', **changes):
        questions = ['Exact formulation %d of the finite bound problem.' % index for index in range(5)]
        return super().build_statement(identifier, statement=questions[0], neighbours=questions[1:], **changes)

    def record_searches(self):
        searches = [binding['search'] for binding in build_fifteen_searches()]
        for search in searches:
            self.put('literature_search', search['id'], search)
        return searches

    def test_measured_shape_delivers_two_disposition_bodies_and_fifteen_original_search_receipts(self):
        from research_harness.problem_openness import export_openness
        native = export_openness(self.store, {'assignment_id': 'turn-1', 'reviewer_id': 'openness-reviewer',
            'minimum': {'searches': [{'formulation': row['question'], 'search_id': row['id']}
                                     for row in self.searches], 'settling_work_id': None,
                        'duplicate_resolutions': []}}, self.root / 'fifteen-search-packet')['native_packet']
        self.assign('turn-1', native)
        self.assertEqual(reading.find_protocol(self.read_packet('turn-1')), V5)
        turns = ['turn-1']
        delivered = []
        while reading.find_stage(self.read_packet(turns[-1])) == 'reading':
            packet = self.read_packet(turns[-1])
            delivered.extend(packet['reading_turn']['reading_plan']['delivered_now'])
            self.invoke(turns[-1], self.build_reading_response(packet, excerpts=[]))
            turns.append('turn-%d' % (len(turns) + 1))
            self.assign(turns[-1], native, prior=turns[-2])
            self.assertLess(len(turns), 12)
        final = self.read_packet(turns[-1])
        self.invoke(turns[-1], self.build_native_result(final, outcome='unresolved'))
        records = self.store.snapshot()['records']
        coverage = reading.verify_problem_coverage(records, self.artifacts,
                                                   records['review_assignment'][turns[-1]], final=True)
        self.assertEqual(coverage['searches'], [digest(row) for row in self.searches])
        dispositions = [row for row in delivered if row.get('field') == 'dispositions']
        self.assertEqual(len({row['record_id'] for row in dispositions}), 2)
        # Different whole lists may have equal prefix slices; v5 shares whole fields, not individual part hashes.
        self.assertEqual(len({(row['record_id'], row['part']) for row in dispositions}), len(dispositions))
        physical = sum(row['characters'] for row in delivered)
        original = sum(len(serialize_record(row).decode()) for row in self.searches)
        self.assertLess(physical, original // 4)
        completed = [row for turn in coverage['turns'] for row in turn.get('completed_searches', [])]
        self.assertEqual(len(completed), 15)


class SearchSharingTests(SearchSharingCase):
    def test_factored_search_bytes_keep_their_independent_required_proof_role(self):
        from research_harness.problem_openness_reviews import actual_delivery
        native = json.loads(self.artifacts.read(self.export(priority=self.versions)))
        sha256 = digest(self.searches[1])
        native['scientific_context']['native_reviews'] = [{'required_evidence_digests': [sha256]}]
        reference = self.artifacts.put(serialize_record(native), 'application/json')
        self.assign('turn-1', reference)
        turns = self.complete(reference)
        final = self.read_packet(turns[-1])
        entry = next(row for row in final['evidence'] if row['artifact']['sha256'] == sha256)
        self.assertEqual(entry['content'].encode(), serialize_record(self.searches[1]))
        self.invoke(turns[-1], self.build_native_result(final))
        records = self.store.snapshot()['records']
        state = protocol.assignment_state(records, self.artifacts, turns[-1])
        self.assertTrue(state['ready'], state['obligations'])
        actual_delivery(records, self.artifacts, state, native, {sha256})
        self.verify(turns[-1])

    def test_final_passage_requests_keep_v4_progress_and_continue_with_original_search_passages(self):
        native = self.start()
        turns = self.complete(native)
        final = turns[-1]
        packet = self.read_packet(final)
        request = {'id': 'original-search-passage', 'record_kind': 'literature_search',
                   'record_id': self.searches[1]['id'], 'anchor': 'This anchor is absent from every search.',
                   'characters': 100}
        self.invoke(final, self.build_reading_response(packet, excerpts=[], passages=[request]))
        refusal = self.store.snapshot()['records']['review_output_refusal']['attempt-' + final]
        self.assertEqual(refusal['code'], 'invalid_reading_response')
        self.assert_error('review_source_continuation_unverified',
                          lambda: self.assign('further-final', native, prior=final))
        request['anchor'] = 'Shared judgment λ with exact'
        self.invoke(final, self.build_reading_response(packet, excerpts=[], passages=[request]),
                    attempt='attempt-valid-final-passage')
        self.assign('further-final', native, prior=final)
        further = self.read_packet('further-final')
        self.assertEqual(reading.find_stage(further), 'final')
        self.assertEqual(further['reading_record']['passages'][0]['status'], 'delivered')
        self.invoke('further-final', self.build_native_result(further))
        self.record_openness('further-final')
        self.assertEqual(assess_openness(self.store.snapshot()['records'], self.artifacts), [])

    def test_preflight_refuses_before_publishing_shared_bodies_or_residuals(self):
        native = json.loads(self.artifacts.read(self.export(priority=self.versions)))
        proof = self.artifacts.put(b'Mandatory proof bytes. ' * 10000, 'text/plain')
        native['evidence'].append({'digest': proof['sha256'], 'kind': 'artifact', 'artifact': proof})
        native['scientific_context']['native_reviews'] = [{'required_evidence_digests': [proof['sha256']]}]
        reference = self.artifacts.put(serialize_record(native), 'application/json')
        before = self.store.snapshot()
        paths = set((self.root / 'research' / 'sources').rglob('*'))
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 150000), self.assertRaises(ResearchError) as caught:
            self.assign('turn-1', reference)
        self.assertEqual(caught.exception.code, 'review_input_too_large')
        self.assertEqual(caught.exception.details['phase'], 'final_preflight')
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(set((self.root / 'research' / 'sources').rglob('*')), paths)

    def test_an_authenticated_but_altered_shared_body_is_delivered_again_without_credit(self):
        original = reading._build_reading_turn
        target = {}
        def alter(*args):
            packet = original(*args)
            unit = next(row for row in packet['reading_turn']['reading_plan']['delivered_now']
                        if row.get('record_kind') == 'search_shared_field')
            target.update(unit)
            entry = next(row for row in packet['evidence'] if row['artifact'] == unit['artifact'])
            entry['content'] = entry['content'][:-1]
            packet.pop('digest')
            packet['digest'] = digest(packet)
            return packet
        with patch.object(reading, '_build_reading_turn', side_effect=alter):
            native = self.start()
        self.invoke('turn-1', self.build_reading_response(self.read_packet('turn-1'), excerpts=[]))
        records = self.store.snapshot()['records']
        state = protocol.assignment_state(records, self.artifacts, 'turn-1')
        self.assertTrue(state['ready'], state['obligations'])
        coverage = reading.verify_problem_coverage(records, self.artifacts, records['review_assignment']['turn-1'])
        self.assertEqual(coverage['searches'], [])
        self.assign('turn-2', native, prior='turn-1')
        now = self.read_packet('turn-2')['reading_turn']['reading_plan']['delivered_now']
        self.assertEqual(now[0], target)

    def test_components_are_delivered_once_and_only_complete_authenticated_coverage_credits_searches(self):
        native = self.start()
        first = self.read_packet('turn-1')['reading_turn']
        manifest = first['reading_record']['search_delivery']
        self.assertEqual(len(manifest['searches']), 3)
        self.assertEqual(first['reading_record']['search_receipts'], [])
        self.invoke('turn-1', self.build_reading_response(self.read_packet('turn-1'), excerpts=[]))
        records = self.store.snapshot()['records']
        partial = reading.verify_problem_coverage(records, self.artifacts, records['review_assignment']['turn-1'])
        self.assertEqual(partial['searches'], [])
        self.assertTrue(all(self.artifacts.read(row['artifact']) for row in manifest['shared_fields']))
        turns = ['turn-1']
        while True:
            turns.append('turn-%d' % (len(turns) + 1))
            self.assign(turns[-1], native, prior=turns[-2])
            packet = self.read_packet(turns[-1])
            if reading.find_stage(packet) == 'final':
                break
            self.invoke(turns[-1], self.build_reading_response(packet, excerpts=[]))
        delivered = []
        for identifier in turns[:-1]:
            turn = self.read_packet(identifier)['reading_turn']
            self.assertEqual(turn['reading_record']['search_delivery'], manifest)
            now = turn['reading_plan']['delivered_now']
            self.assertLessEqual(sum(row['characters'] for row in now), 120000)
            delivered.extend(now)
        components = [row for row in delivered if row.get('record_kind') == 'search_shared_field'
                      and row['field'] == 'dispositions']
        self.assertGreater(len(components), 1)
        self.assertEqual([row['part'] for row in components], list(range(1, components[0]['parts'] + 1)))
        self.assertTrue(all(len(row['owners']) == 2 for row in components))
        self.assertEqual(len({(row['record_id'], row['part']) for row in delivered
                              if row['kind'] == 'record'}), len([row for row in delivered if row['kind'] == 'record']))
        self.invoke(turns[-1], self.build_native_result(self.read_packet(turns[-1])))
        receipts = self.verify(turns[-1])
        completed = [row for turn in receipts for row in turn.get('completed_searches', [])]
        self.assertEqual({row['search_digest'] for row in completed}, {digest(row) for row in self.searches})
        self.assertTrue(all(row['components'] and all(item['deliveries'] for item in row['components'])
                            for row in completed))

    def test_shared_field_excerpts_and_passages_cite_original_search_bytes_and_offsets(self):
        original = serialize_record(self.searches[1]).decode()
        anchor = 'Shared judgment λ with exact'
        start = original.index(anchor)
        phrase = original[start:start + 90].rstrip()
        def respond(packet, number):
            if number != 2:
                return self.build_reading_response(packet, excerpts=[])
            return self.build_reading_response(packet, excerpts=[{'record_kind': 'literature_search',
                'record_id': self.searches[1]['id'], 'text': phrase, 'purpose': 'The original judgment.'}],
                passages=[{'id': 'search-passage', 'record_kind': 'literature_search',
                           'record_id': self.searches[1]['id'], 'anchor': anchor, 'characters': 140}])
        turns = self.complete(respond=respond)
        record = self.read_packet(turns[-1])['reading_record']
        [excerpt], [passage] = record['excerpts'], record['passages']
        self.assertEqual((excerpt['status'], excerpt['start'], excerpt['end'], excerpt['text']),
                         ('verified', start, start + len(phrase), phrase))
        self.assertEqual((passage['status'], passage['start'], passage['text']),
                         ('delivered', start, original[start:start + 140]))
        self.assertEqual(excerpt['sha256'], hashlib.sha256(phrase.encode()).hexdigest())
        self.assertEqual(record['sources'][0]['artifact_descriptor']['sha256'], digest(self.searches[1]))
        self.invoke(turns[-1], self.build_native_result(self.read_packet(turns[-1])))
        self.verify(turns[-1])

    def test_roomy_final_keeps_bindings_manifest_and_receipts_without_expanding_factored_searches(self):
        turns = self.complete()
        final = self.read_packet(turns[-1])
        self.assertEqual(len(final['native']['scientific_context']['openness_minimum']['searches']), 3)
        record = final['reading_record']
        entries = {row['artifact']['sha256']: row for row in final['evidence']}
        factored = [row for row in record['search_delivery']['searches'] if row['residual'] is not None]
        self.assertEqual(len(factored), 3)
        for row in factored:
            self.assertEqual(entries[row['search_digest']]['content'], '')
            self.assertEqual(entries[row['search_digest']]['delivery'], 'search-rebuilt-from-reading-turns')
        self.assertEqual({row['search_digest'] for row in record['search_receipts']},
                         {digest(row) for row in self.searches})
        self.assertNotIn('Shared judgment', json.dumps(final, ensure_ascii=False))
        self.invoke(turns[-1], self.build_native_result(final))
        _, sent = self.read_sent_packet(turns[-1])
        self.assertEqual(sent['reading_record'], record)
        self.verify(turns[-1])

    def test_end_to_end_finding_reassessment_gate_and_result_rows_keep_original_searches(self):
        from research_harness.problem_delivery import assess_minimum
        from research_harness.problem_evidence import describe_openness_searches, project_citations
        turns = self.complete()
        self.invoke(turns[-1], self.build_native_result(self.read_packet(turns[-1])))
        saved = self.record_openness(turns[-1])
        records = self.store.snapshot()['records']
        self.assertEqual(assess_openness(records, self.artifacts), [])
        rows = describe_openness_searches(records, saved)
        self.assertEqual([row['search'] for row in rows], self.searches)
        self.assertTrue(all(set(row) == {'formulation', 'search_digest', 'search', 'sources'} for row in rows))
        self.assertTrue(project_citations(records, self.artifacts))
        self.assertFalse(any(row['code'].startswith('problem_openness')
                             for row in assess_minimum(records, self.artifacts)))
        minimum_fixtures.ProblemOpennessMinimumTests.search(self, self.versions)
        [stale] = assess_openness(self.store.snapshot()['records'], self.artifacts)
        self.assertEqual((stale['code'], stale['cause']), ('problem_openness_stale', 'openness_search_stale'))

    def assert_changed_final_rejected(self, change):
        original = reading._build_final_turn
        def changed(*args):
            packet = original(*args)
            change(packet)
            packet.pop('digest')
            packet['digest'] = digest(packet)
            return packet
        with patch.object(reading, '_build_final_turn', side_effect=changed):
            turns = self.complete()
        self.invoke(turns[-1], self.build_native_result(self.read_packet(turns[-1])))
        with self.assertRaises(ResearchError):
            self.verify(turns[-1])

    def test_changed_manifest_is_rejected(self):
        self.assert_changed_final_rejected(lambda p: p['reading_record']['search_delivery']['searches'][0].update(
            search_digest='a' * 64))

    def test_changed_notes_are_rejected(self):
        self.assert_changed_final_rejected(lambda p: p['reading_record']['turns'][0].update(
            notes='Invented earlier note.'))

    def test_forged_completion_receipts_are_rejected(self):
        self.assert_changed_final_rejected(lambda p: p['reading_record']['search_receipts'].clear())

    def test_changed_retained_excerpt_is_rejected(self):
        self.assert_changed_final_rejected(lambda p: p['reading_record']['excerpts'].append(
            {'text': 'Invented retained text.'}))

    def test_changed_allowance_is_rejected(self):
        self.assert_changed_final_rejected(lambda p: p['reading_record']['allowances']['excerpts'].update(used=999))

    def test_changed_minimum_is_rejected(self):
        self.assert_changed_final_rejected(lambda p: p['native']['scientific_context']['openness_minimum'][
            'searches'].pop())

    def test_unauthorized_search_omission_is_rejected(self):
        self.assert_changed_final_rejected(lambda p: next(row for row in p['evidence'] if row.get('delivery') ==
            'search-rebuilt-from-reading-turns').update(delivery='invented-omission'))

    def test_another_reviewer_route_chain_or_invalid_attempt_cannot_supply_coverage(self):
        turns = self.complete()
        self.invoke(turns[-1], self.build_native_result(self.read_packet(turns[-1])))
        records = self.store.snapshot()['records']
        for name in ('reviewer', 'route', 'author', 'chain', 'packet', 'invalid_attempt'):
            with self.subTest(boundary=name):
                changed = copy.deepcopy(records)
                assignment = changed['review_assignment'][turns[1]]
                if name == 'invalid_attempt':
                    changed['review_attempt']['attempt-' + turns[1]]['origin'] = 'imported'
                elif name == 'chain':
                    assignment['reading_chain']['first_assignment_id'] = 'another-first-assignment'
                elif name == 'packet':
                    assignment['context']['native_packet'] = dict(assignment['context']['native_packet'], sha256='a' * 64)
                else:
                    assignment[name + '_id'] = 'another-' + name
                with self.assertRaises(ResearchError):
                    reading.verify_chain(changed, self.artifacts, changed['review_assignment'][turns[-1]])
