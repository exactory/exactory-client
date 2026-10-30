"""A math-solver result review whose openness minimum is too large for one turn is read as an openness reading chain."""

import copy
import types
import unittest
from contextlib import ExitStack

from research_harness import openness_reading, review_excerpts, review_protocol as protocol
from research_harness import review_transport as transport
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.literature import import_bundle
from research_harness.reading import record_reading
from search_controller.errors import SearchError
from search_controller.reviewer import require_review
from search_controller.storage import ContentSink
from tests.openness_chain_support import MathOpennessChainCase, OBJECTIVE_PACKET_BYTES, build_filler


class MathOpennessReadingTests(MathOpennessChainCase, unittest.TestCase):
    def check_review(self, subject, review=None, **options):
        state = self.controller.status()
        return require_review(self.root, state, ContentSink(self.controller.store),
                              self.review if review is None else review, subject=subject, new=True, **options)

    def read_receipts(self, final):
        records = self.store.snapshot()['records']
        return openness_reading.verify_chain(records, self.artifacts, records['review_assignment'][final])

    def assert_objective_chain(self, adapter):
        ordinary, size = self.build_objective_fixture(adapter)
        self.assertEqual(size, OBJECTIVE_PACKET_BYTES)
        limit = self.find_input_limit()
        ordinary_request = transport.request_body(self.store.snapshot()['records']['review_route']['route'],
                                                  protocol.canonical_prompt('native_math')['text'], ordinary)
        self.assertGreater(self.measure_request(ordinary_request), limit)
        turns = self.run_chain()
        sizes, fresh, total = self.measure_chain(turns)
        self.assertEqual(sum(fresh), total)
        self.assertTrue(all(characters <= limit for characters in sizes), (limit, sizes))
        self.assertTrue(all(characters <= review_excerpts.TURN_FULLTEXT_CHARACTERS for characters in fresh), fresh)
        self.assertEqual(fresh[-1], 0)
        final = self.read_turn(turns[-1])
        self.assertEqual(final['native'], ordinary['native'])
        self.assertEqual(final['reading_record']['protocol'], openness_reading.MATH_PROTOCOL)
        self.assertTrue(all(row['status'] == 'verified' for row in final['reading_record']['excerpts']))
        self.accept_result()
        status = self.controller.status(full_audit=True)
        self.assertEqual(status['freshness']['status'], 'fresh')
        accepted = status['acceptances']['acceptance-000001']
        self.assertEqual(accepted['status'], 'accepted')
        self.assertEqual(accepted['reading_turns'], self.read_receipts(turns[-1]))
        self.assertEqual([turn['assignment_id'] for turn in accepted['reading_turns']], turns)
        records = self.store.snapshot()['records']
        responses = [records['source'][source]['response']['size'] for source in self.sources]
        print('objective_fixture adapter=%s packet_bytes=%d response_bytes=%s input_limit=%d unit_characters=%d '
              'turns=%d max_new_unit_characters=%d request_characters=%s new_unit_characters=%s'
              % (adapter, size, responses, limit, total, len(turns), max(fresh), sizes, fresh))

    def test_the_objective_sized_minimum_is_read_accepted_and_audited_on_the_codex_route(self):
        self.assert_objective_chain('codex_cli_v1')

    def test_the_objective_sized_minimum_is_read_accepted_and_audited_on_the_claude_route(self):
        self.assert_objective_chain('claude_cli_v1')

    def test_a_response_larger_than_either_route_input_arrives_in_consecutive_parts(self):
        self.build_fixture(response_sizes=(1400000,), text_sizes=(20000,), adapter='claude_cli_v1')
        response = self.store.snapshot()['records']['source'][self.sources[0]]['response']['size']
        limit = self.find_input_limit()
        self.assertGreater(response, max(limit, transport.CODEX_INPUT_LIMIT_CHARS))
        turns = self.run_chain()
        sizes, fresh, total = self.measure_chain(turns)
        self.assertEqual(sum(fresh), total)
        self.assertTrue(all(characters <= limit for characters in sizes), sizes)
        self.assertTrue(all(characters <= review_excerpts.TURN_FULLTEXT_CHARACTERS for characters in fresh), fresh)
        receipts = self.read_receipts(turns[-1])
        parts = [(part['part'], part['parts']) for turn in receipts[:-1] for part in turn.get('delivered_parts', [])
                 if part.get('source_id') == self.sources[0]]
        self.assertEqual(parts, [(number, 12) for number in range(1, 13)])
        self.accept_result()
        accepted = self.controller.status(full_audit=True)['acceptances']['acceptance-000001']
        self.assertEqual((accepted['status'], accepted['reading_turns']), ('accepted', receipts))

    def test_a_search_only_minimum_is_read_as_a_chain(self):
        self.build_fixture(response_sizes=(70000, 60000), text_sizes=())
        turns = self.run_chain()
        self.accept_result()
        accepted = self.controller.status(full_audit=True)['acceptances']['acceptance-000001']
        kinds = {unit['kind'] for turn in accepted['reading_turns'] for unit in turn['delivered_units']}
        self.assertEqual(kinds, {'search_response', 'record'})
        responses = [unit['source_id'] for turn in accepted['reading_turns'][:-1] for unit in turn['delivered_units']
                     if unit['kind'] == 'search_response']
        self.assertEqual(responses, self.sources)
        self.assertEqual(len(turns), 3)

    def test_a_packet_that_fits_the_route_with_more_than_one_turn_of_text_is_read_as_a_chain(self):
        ordinary = self.build_fixture(response_sizes=(20000,), text_sizes=(110000,))
        route = self.store.snapshot()['records']['review_route']['route']
        request = transport.request_body(route, protocol.canonical_prompt('native_math')['text'], ordinary)
        self.assertLessEqual(self.measure_request(request), self.find_input_limit())
        turns = self.run_chain()
        self.assertGreater(len(turns), 2)
        sizes, fresh, total = self.measure_chain(turns)
        self.assertEqual(sum(fresh), total)
        self.assertTrue(all(characters <= review_excerpts.TURN_FULLTEXT_CHARACTERS for characters in fresh), fresh)
        self.accept_result()
        self.assertEqual(self.controller.status(full_audit=True)['acceptances']['acceptance-000001']['status'],
                         'accepted')

    def test_a_checkpoint_chain_and_a_closure_chain_complete_the_objective_with_their_receipts(self):
        self.build_fixture(adapter='claude_cli_v1')
        turns = self.run_chain()
        packet = self.read_turn('turn-1')
        units = packet['reading_turn']['reading_plan']['delivered_now'] + packet['reading_turn']['reading_plan'][
            'remaining']
        self.assertNotIn('problem_statement', {unit.get('record_kind') for unit in units})
        self.assertEqual(packet['reading_turn']['statement'],
                         self.controller.status()['checkpoints']['checkpoint-000001'])
        self.assertEqual([unit['kind'] for unit in units][:2], ['search_response', 'search_response'])
        self.accept_result()
        accepted = self.controller.status()['acceptances']['acceptance-000001']
        self.assertEqual(accepted['reading_turns'], self.read_receipts(turns[-1]))
        self.assertEqual(accepted['reading_turns'][-1]['assignment_id'], turns[-1])
        spec = self.build_closure()
        closure_turns = self.run_chain(first='closure-1')
        self.assertEqual(self.read_turn('closure-1')['reading_turn']['statement'], spec['subject'])
        self.complete_objective(spec)
        completed = self.controller.status(full_audit=True)
        self.assertEqual((completed['proof_status'], completed['openness']['status']), ('proved', 'accepted'))
        self.assertEqual(completed['freshness']['status'], 'fresh')
        closure = completed['control']['closure']
        self.assertEqual(closure['review'], self.review)
        self.assertEqual(closure['reading_turns'], self.read_receipts(closure_turns[-1]))
        self.assertEqual([turn['assignment_id'] for turn in closure['reading_turns']], closure_turns)
        self.assertEqual(completed['acceptances']['acceptance-000001']['reading_turns'], accepted['reading_turns'])

    def assert_missing_unit_refused(self, row_filter, part=None):
        self.build_fixture(response_sizes=(130000, 60000), text_sizes=(20000,))
        with ExitStack() as stack:
            for patcher in self.drop_unit(row_filter, part):
                stack.enter_context(patcher)
            turns = self.run_chain()
        with self.assertRaises(ResearchError) as raised:
            self.read_receipts(turns[-1])
        self.assertEqual(raised.exception.code, 'openness_reading_incomplete')
        missing = raised.exception.details['units']
        self.assertEqual(len(missing), 1)
        self.assertTrue(row_filter(missing[0]))
        if part is not None:
            self.assertEqual(missing[0]['missing_parts'], [part])
        before = self.controller.store.read()
        checkpoint = self.controller.status()['checkpoints']['checkpoint-000001']
        for check in (lambda: self.check_review(checkpoint), self.accept_result):
            with self.assertRaises(SearchError) as caught:
                check()
            self.assertEqual((caught.exception.code, caught.exception.details['cause']),
                             ('review_context_unverified', 'openness_reading_incomplete'))
        self.assertEqual(self.controller.store.read(), before)

    def test_a_chain_missing_a_captured_response_blocks_acceptance(self):
        self.assert_missing_unit_refused(lambda value: value.get('source_id') == self.sources[1])

    def test_a_chain_missing_a_full_text_blocks_acceptance(self):
        self.assert_missing_unit_refused(lambda value: value.get('version_id') == self.versions[0])

    def test_a_chain_missing_a_search_record_blocks_acceptance(self):
        self.assert_missing_unit_refused(lambda value: value.get('record_kind') == 'literature_search'
                                         and value.get('record_id') == 'search-2')

    def test_a_chain_missing_a_reading_record_blocks_acceptance(self):
        self.assert_missing_unit_refused(lambda value: value.get('record_kind') == 'reading')

    def test_a_chain_missing_one_part_of_a_response_blocks_acceptance(self):
        self.assert_missing_unit_refused(lambda value: value.get('source_id') == self.sources[0], part=2)

    def test_a_closure_chain_missing_a_unit_blocks_completion(self):
        self.build_fixture(response_sizes=(130000, 60000), text_sizes=(20000,))
        self.run_chain()
        self.accept_result()
        checks = (('response', lambda value: value.get('source_id') == self.sources[1], None),
                  ('text', lambda value: value.get('version_id') == self.versions[0], None),
                  ('search-record', lambda value: value.get('record_id') == 'search-1', None),
                  ('reading-record', lambda value: value.get('record_kind') == 'reading', None),
                  ('part', lambda value: value.get('source_id') == self.sources[0], 1))
        for name, row_filter, part in checks:
            with self.subTest(missing=name):
                spec = self.build_closure('closure-%s-1' % name)
                with ExitStack() as stack:
                    for patcher in self.drop_unit(row_filter, part):
                        stack.enter_context(patcher)
                    self.run_chain(first='closure-%s-1' % name)
                before = self.controller.store.read()
                with self.assertRaises(SearchError) as caught:
                    self.complete_objective(spec)
                self.assertEqual((caught.exception.code, caught.exception.details['cause']),
                                 ('review_context_unverified', 'openness_reading_incomplete'))
                self.assertEqual(self.controller.store.read(), before)
        self.assertEqual(self.controller.status()['proof_status'], 'open')

    def test_a_failed_turn_replaced_by_a_new_run_completes_the_chain(self):
        self.build_fixture()
        self.assign_turn('turn-1')
        self.invoke('turn-1', self.build_reading_response(self.read_turn('turn-1')))
        self.assign_turn('turn-2', 'turn-1')
        self.fail_invocation('turn-2')
        self.assign_turn('turn-2b', 'turn-1')
        self.invoke('turn-2b', self.build_reading_response(self.read_turn('turn-2b')))
        self.assign_turn('turn-3', 'turn-2b')
        self.assertIn('reading_record', self.read_turn('turn-3'))
        self.invoke('turn-3', self.review)
        records = self.store.snapshot()['records']
        self.assertEqual(openness_reading.find_final_assignment(records, self.artifacts, 'turn-1')['id'], 'turn-3')
        self.accept_result()
        accepted = self.controller.status(full_audit=True)['acceptances']['acceptance-000001']
        self.assertEqual([turn['assignment_id'] for turn in accepted['reading_turns']], ['turn-1', 'turn-2b', 'turn-3'])
        self.assertEqual(self.store.snapshot()['records']['review_attempt']['attempt-turn-2']['status'], 'failed')

    def test_a_competing_live_continuation_is_refused(self):
        self.build_fixture()
        turns = self.run_chain()
        with self.assertRaises(ResearchError) as raised:
            self.assign_turn('turn-2x', 'turn-1')
        self.assertEqual(raised.exception.code, 'review_source_continuation_fork')
        records = copy.deepcopy(self.store.snapshot()['records'])
        competing = dict(records['review_assignment']['turn-2'], id='turn-2x',
                         recorded_revision=self.store.revision + 1)
        records['review_assignment']['turn-2x'] = competing
        with self.assertRaises(ResearchError) as raised:
            openness_reading.find_final_assignment(records, self.artifacts, 'turn-1')
        self.assertEqual(raised.exception.code, 'review_source_continuation_fork')
        guard = types.SimpleNamespace(root=self.store.root, snapshot=lambda: {'records': records})
        checkpoint = self.controller.status()['checkpoints']['checkpoint-000001']
        with self.assertRaises(SearchError) as caught:
            self.check_review(checkpoint, common_guard=guard)
        self.assertEqual(caught.exception.details['cause'], 'review_source_continuation_fork')
        self.assertEqual(self.check_review(checkpoint)['reading_turns'], self.read_receipts(turns[-1]))

    def test_a_final_reading_response_without_a_deliverable_passage_is_refused_and_can_be_corrected(self):
        self.build_fixture()
        absent = {'id': 'missing', 'source_id': self.sources[0], 'characters': 80,
                  'anchor': 'This verbatim phrase is absent from the saved response.'}
        turns = self.run_chain(lambda packet: self.build_reading_response(packet, [absent]))
        state = protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, turns[-1])
        self.assertFalse(state['ready'])
        self.assertEqual(state['refused_attempts'], [{'attempt_id': 'attempt-' + turns[-1],
                                                    'code': 'invalid_reading_response'}])
        with self.assertRaises(ResearchError) as raised:
            self.assign_turn('passage-final', turns[-1])
        self.assertEqual(raised.exception.code, 'review_source_continuation_unverified')
        present = {'id': 'present', 'source_id': self.sources[0], 'characters': 80,
                   'anchor': self.review['findings']['openness']['minimum']['statement']}
        self.invoke(turns[-1], self.build_reading_response(self.read_turn(turns[-1]), [present]),
                    attempt_id='attempt-final-retry')
        self.assertTrue(protocol.assignment_state(self.store.snapshot()['records'], self.artifacts,
                                                  turns[-1])['ready'])
        self.assign_turn('passage-final', turns[-1])
        passages = self.read_turn('passage-final')['reading_record']['passages']
        body = self.artifacts.read(self.store.snapshot()['records']['source'][self.sources[0]]['response']).decode()
        start = body.index(present['anchor'])
        self.assertEqual([(row['status'], row['text']) for row in passages],
                         [('delivered', body[start:start + 80])])
        self.invoke('passage-final', self.review)
        self.accept_result()
        accepted = self.controller.status(full_audit=True)['acceptances']['acceptance-000001']
        self.assertEqual(accepted['reading_turns'][-1]['assignment_id'], 'passage-final')

    def test_a_final_passage_over_the_remaining_allowance_is_refused_and_can_be_retried(self):
        self.build_fixture()
        requests = [{'id': 'passage-%d' % number, 'source_id': self.sources[0], 'characters': 19999,
                     'anchor': self.review['findings']['openness']['minimum']['statement']} for number in range(5)]
        turns = self.run_chain(lambda packet: self.build_reading_response(packet, requests))
        self.assign_turn('passage-final', turns[-1])
        packet = self.read_turn('passage-final')
        self.assertEqual(packet['reading_record']['allowances']['passages']['used'], 99995)
        request = dict(requests[0], id='too-much', characters=80)
        self.invoke('passage-final', self.build_reading_response(packet, [request]))
        state = protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, 'passage-final')
        self.assertFalse(state['ready'])
        self.assertEqual(state['refused_attempts'], [{'attempt_id': 'attempt-passage-final',
                                                    'code': 'invalid_reading_response'}])
        self.invoke('passage-final', self.review, attempt_id='attempt-final-retry')
        self.accept_result()
        self.assertEqual(self.controller.status(full_audit=True)['acceptances']['acceptance-000001']['status'],
                         'accepted')

    def test_a_contaminated_turn_invalidates_the_acceptance_in_a_full_audit_without_rewriting_it(self):
        self.build_fixture()
        self.run_chain()
        self.accept_result()
        accepted = copy.deepcopy(self.controller.status()['acceptances']['acceptance-000001'])
        protocol.record_context_event(self.store, {'id': 'exposure', 'assignment_id': 'turn-1',
            'kind': 'contamination', 'source': 'author_history', 'reason': 'An author note reached this reading turn.',
            'evidence': [self.texts[self.versions[0]]]}, expected_revision=self.store.revision,
            request_id='contaminate-turn-1')
        document = self.controller.store.read()
        audited = self.controller.status(full_audit=True)
        checked = audited['acceptances']['acceptance-000001']
        self.assertEqual(checked['status'], 'invalidated')
        self.assertEqual(audited['freshness']['failures']['acceptance-000001']['code'], 'review_context_unverified')
        self.assertEqual({key: value for key, value in checked.items() if key != 'status'},
                         {key: value for key, value in accepted.items() if key != 'status'})
        self.assertEqual(self.controller.store.read(), document)
        self.assertEqual(self.controller.status()['acceptances']['acceptance-000001'], accepted)

    def test_every_pinned_original_of_a_version_is_read_and_a_later_capture_is_not(self):
        self.build_fixture(response_sizes=(70000,), text_sizes=(60000,))
        version = self.versions[0]
        quote = 'Theorem 2. A distinct original proves a different finite bound.'
        capture = self.case.capture(version, body=quote + ' ' + build_filler('h-', 60000) + ' References: none.')
        bundle = self.case.bundle(version, capture=capture, bundle_id='second-original')
        for unit in bundle['units']:
            unit['link'] = self.case.link(version, capture['source_id'], capture['text'],
                                          unit['link']['locator']['quote'], kind='span')
        bundle['inventory']['links'] = [bundle['units'][0]['link']]
        self.case.mutate(import_bundle, bundle)
        self.case.mutate(record_reading, self.case.full_note(bundle, 'read-second-original'))
        readings = [digest(row) for row in self.store.snapshot()['records']['reading'].values()]
        finding = self.review['findings']['openness']
        finding['minimum']['reading_digests'] = readings
        finding['evidence'] = list(dict.fromkeys(finding['evidence'] + readings))
        self.export_fixture(self.controller.status()['checkpoints']['checkpoint-000001'])
        pinned = {self.texts[version]['sha256'], capture['text']['sha256']}
        ambiguous = {'id': 'ambiguous', 'version_id': version, 'anchor': quote, 'characters': 80}
        turns = self.run_chain(lambda packet: self.build_reading_response(packet, [ambiguous]))
        self.assertEqual(protocol.assignment_state(self.store.snapshot()['records'], self.artifacts,
                                                   turns[-1])['refused_attempts'],
                         [{'attempt_id': 'attempt-' + turns[-1], 'code': 'invalid_reading_response'}])
        exact = dict(ambiguous, id='exact', text_sha256=capture['text']['sha256'])
        self.invoke(turns[-1], self.build_reading_response(self.read_turn(turns[-1]), [exact]),
                    attempt_id='attempt-final-retry')
        self.assign_turn('passage-final', turns[-1])
        record = self.read_turn('passage-final')['reading_record']
        self.assertEqual([(row['status'], row['text_sha256']) for row in record['passages']],
                         [('delivered', capture['text']['sha256'])])
        self.assertEqual({row['text_sha256'] for row in record['excerpts']
                          if row.get('version_id') == version and row['status'] == 'verified'}, pinned)
        self.invoke('passage-final', self.review)
        self.accept_result()
        accepted = self.controller.status(full_audit=True)['acceptances']['acceptance-000001']
        read = {unit['text_sha256'] for turn in accepted['reading_turns'][:-2] for unit in turn['delivered_units']
                if unit['kind'] == 'fulltext'}
        self.assertEqual(read, pinned)
        self.case.capture(version, body='A later original with new material. References: none.')
        native = self.read_turn('passage-final')['native']
        rows = openness_reading.list_reading_rows(self.store.snapshot()['records'], self.artifacts, native)
        self.assertEqual({row['text_sha256'] for row in rows if row['kind'] == 'fulltext'}, pinned)
        self.assertEqual(self.controller.status(full_audit=True)['acceptances']['acceptance-000001'], accepted)

    def test_a_small_math_minimum_stays_a_single_native_turn(self):
        self.build_fixture(response_sizes=(2000,), text_sizes=(1000,))
        assignment = self.assign_turn('turn-1')
        self.assertNotIn('reading_chain', assignment)
        self.assertEqual(assignment['prompt'], protocol.canonical_prompt('native_math'))
        self.invoke('turn-1', self.review)
        self.accept_result()
        accepted = self.controller.status(full_audit=True)['acceptances']['acceptance-000001']
        self.assertEqual(accepted['status'], 'accepted')
        self.assertNotIn('reading_turns', accepted)


if __name__ == '__main__':
    unittest.main()
