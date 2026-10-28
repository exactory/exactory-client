"""A problem openness review whose full texts exceed the route input reads them over several turns."""

import json
from unittest.mock import patch

from openness_reading_fixtures import OpennessReadingCase, build_theorem
from research_harness import openness_reading, review_protocol as protocol, review_transport as transport
from research_harness import review_codex_transport as codex


class OpennessReadingChainTests(OpennessReadingCase):
    def complete_text(self, entry, version):
        body = self.artifacts.read(self.texts[version]).decode()
        return entry.get('encoding') == 'utf-8' and entry.get('content') == body

    def delivered_versions(self, sent):
        entries = {entry['artifact']['sha256']: entry for entry in sent['evidence']}
        return [version for version in self.versions
                if self.texts[version]['sha256'] in entries
                and self.complete_text(entries[self.texts[version]['sha256']], version)]

    def record_openness(self, identifier):
        return self.mutate(self.load_problem_module().record_openness, {'assignment_id': identifier})['result']

    def test_an_openness_packet_above_the_route_limit_becomes_a_reading_chain(self):
        self.limit_input()
        assignment = self.assign('turn-1', self.export())
        self.assertEqual(assignment['reading_chain'], {'first_assignment_id': 'turn-1', 'turn': 1})
        self.assertEqual((assignment['history'], assignment['session_id']), ([], 'turn-1'))
        self.assertEqual(assignment['prompt']['template_id'], openness_reading.PROTOCOL + '/reading')
        self.assertEqual(assignment['prompt'], protocol.canonical_prompt('native_math', reading_stage='reading'))
        packet = self.read_packet('turn-1')
        self.assertNotIn('native', packet)
        turn = packet['reading_turn']
        native = json.loads(self.artifacts.read(assignment['context']['native_packet']))
        for key in ('subject_digest', 'claim_digest', 'reviewer'):
            self.assertEqual(turn[key], native[key])
        self.assertEqual(turn['statement'], self.selected)
        self.assertEqual(turn['formulations'], {'statement': self.selected['statement'], 'neighbours': []})
        plan = turn['reading_plan']
        self.assertEqual(plan['turn'], 1)
        self.assertEqual([row['version_id'] for row in plan['delivered_now']], self.versions[:2])
        self.assertEqual(plan['delivered_before'], [])
        self.assertEqual([row['version_id'] for row in plan['remaining']], self.versions[2:])
        self.assertEqual(turn['reading_record']['turns'], [])
        self.assertEqual(turn['reading_record']['allowances'], {'excerpts': {'characters': 200000, 'used': 0},
                                                                'passages': {'characters': 100000, 'used': 0}})
        # Only the full texts of this turn are evidence; the search and reading records wait for the final turn.
        self.assertEqual(sorted(entry['artifact']['sha256'] for entry in packet['evidence']),
                         sorted(self.texts[version]['sha256'] for version in self.versions[:2]))

    def test_batches_follow_the_text_priority_and_deliver_every_body(self):
        self.limit_input()
        native_packet = self.export(priority=[self.versions[2], self.versions[0]])
        assignment = self.assign('turn-1', native_packet)
        plan = self.read_packet('turn-1')['reading_turn']['reading_plan']
        self.assertEqual([row['version_id'] for row in plan['delivered_now']], [self.versions[2], self.versions[0]])
        self.assertEqual([row['version_id'] for row in plan['remaining']], [self.versions[1]])
        records = self.store.snapshot()['records']
        route = records['review_route']['route']
        with transport.text_only_delivery(True):
            ordinary = protocol.build_packet(records, self.artifacts, None, 'native_math', 'openness-reviewer',
                                             native_packet=native_packet)
            rebuilt, _ = openness_reading.prepare_packet(records, self.artifacts, assignment, route, ordinary)
        self.assertEqual(json.dumps(rebuilt, sort_keys=True), json.dumps(self.read_packet('turn-1'), sort_keys=True))
        self.invoke('turn-1', self.build_reading_response(self.read_packet('turn-1')))
        request, sent = self.read_sent_packet('turn-1')
        self.assertLessEqual(len(codex.packet_text(request)), transport.CODEX_INPUT_LIMIT_CHARS)
        self.assertEqual(self.delivered_versions(sent), sorted([self.versions[2], self.versions[0]]))
        state = protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, 'turn-1')
        self.assertTrue(state['ready'], state['obligations'])

    def test_a_malformed_reading_response_is_refused_and_the_turn_runs_again(self):
        self.limit_input()
        native_packet = self.export()
        self.assign('turn-1', native_packet)
        packet = self.read_packet('turn-1')
        malformed = dict(self.build_reading_response(packet), notes='')
        self.invoke('turn-1', malformed)
        refusal = self.store.snapshot()['records']['review_output_refusal']['attempt-turn-1']
        self.assertEqual((refusal['code'], refusal['check']), ('invalid_reading_response', 'native.output'))
        short_anchor = {'id': 'p', 'version_id': self.versions[0], 'anchor': 'short', 'characters': 10}
        for change in ({'stage': 'final'}, {'claim_digest': '0' * 64}, {'notes': 'x' * 20001},
                       {'passage_requests': [short_anchor]}):
            with self.subTest(change=change), self.assertRaises(Exception) as raised:
                openness_reading.validate_turn_output(packet, dict(self.build_reading_response(packet), **change))
            self.assertEqual(raised.exception.code, 'invalid_reading_response')
        self.assert_error('review_source_continuation_unverified',
                          lambda: self.assign('turn-2', native_packet, prior='turn-1'))
        self.invoke('turn-1', self.build_reading_response(packet), attempt='attempt-turn-1-retry')
        state = protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, 'turn-1')
        self.assertTrue(state['ready'], state['obligations'])
        self.assertEqual(state['attempt_id'], 'attempt-turn-1-retry')

    def test_the_final_turn_carries_the_reading_record(self):
        self.limit_input()
        native_packet = self.export(priority=self.versions)
        turns = self.run_reading_turns(native_packet)
        self.assertEqual(turns, ['turn-1', 'turn-2', 'turn-3'])
        second = self.read_packet('turn-2')['reading_turn']
        self.assertEqual([row['version_id'] for row in second['reading_plan']['delivered_before']], self.versions[:2])
        self.assertEqual([row['version_id'] for row in second['reading_plan']['delivered_now']], self.versions[2:])
        assignment = self.store.snapshot()['records']['review_assignment']['turn-3']
        self.assertEqual(assignment['reading_chain'], {'first_assignment_id': 'turn-1', 'turn': 3})
        self.assertEqual(assignment['prompt']['template_id'], openness_reading.PROTOCOL + '/final')
        self.assertTrue(assignment['prompt']['text'].startswith(protocol.canonical_prompt('native_math')['text']))
        packet = self.read_packet('turn-3')
        native = json.loads(self.artifacts.read(native_packet))
        self.assertEqual(packet['native'], native)
        record = packet['reading_record']
        self.assertEqual([(row['assignment_id'], row['attempt_id'], row['delivered']) for row in record['turns']],
                         [('turn-1', 'attempt-turn-1', self.versions[:2]),
                          ('turn-2', 'attempt-turn-2', self.versions[2:])])
        self.assertTrue(all(row['notes'] for row in record['turns']))
        self.assertEqual([(row['status'], row['version_id']) for row in record['excerpts']],
                         [('verified', version) for version in self.versions])
        self.assertEqual([row['text'] for row in record['excerpts']], [build_theorem(number) for number in (1, 2, 3)])
        self.assertEqual(record['allowances']['excerpts']['used'], sum(len(build_theorem(n)) for n in (1, 2, 3)))
        self.assertEqual(record['passages'], [])
        records = self.store.snapshot()['records']
        route = records['review_route']['route']
        request = transport.request_body(route, assignment['prompt']['text'], packet)
        self.assertLessEqual(len(codex.packet_text(request)), transport.CODEX_INPUT_LIMIT_CHARS)

    def test_a_final_turn_with_passage_requests_leads_to_a_further_final_turn(self):
        self.limit_input()
        native_packet = self.export(priority=self.versions)
        final = self.run_reading_turns(native_packet)[-1]
        anchor = build_theorem(2)[:40]
        passage = {'id': 'theorem-2', 'version_id': self.versions[1], 'anchor': anchor, 'characters': 300}
        self.invoke(final, self.build_reading_response(self.read_packet(final), excerpts=[], passages=[passage]))
        records = self.store.snapshot()['records']
        self.assertNotIn('attempt-' + final, records.get('review_output_refusal', {}))
        further = self.assign('turn-4', native_packet, prior=final)
        self.assertEqual(further['reading_chain'], {'first_assignment_id': 'turn-1', 'turn': 4})
        self.assertEqual(further['prompt']['template_id'], openness_reading.PROTOCOL + '/final')
        record = self.read_packet('turn-4')['reading_record']
        [answer] = record['passages']
        body = self.artifacts.read(self.texts[self.versions[1]]).decode()
        start = body.index(build_theorem(2))
        self.assertEqual((answer['request'], answer['status'], answer['text']),
                         (passage, 'delivered', body[start:start + 300]))
        self.assertEqual(record['allowances']['passages']['used'], 300)
        self.assertEqual(record['turns'][-1]['assignment_id'], final)
        self.assertEqual(record['turns'][-1]['delivered'], [])
        self.invoke('turn-4', self.build_native_result(self.read_packet('turn-4')))
        saved = self.record_openness('turn-4')
        self.assertEqual([row['assignment_id'] for row in saved['reading_turns']], ['turn-1', 'turn-2', 'turn-3'])

    def test_a_final_reading_answer_needs_a_passage_request_and_passage_allowance(self):
        self.limit_input()
        final = self.run_reading_turns(self.export(priority=self.versions))[-1]
        packet = self.read_packet(final)
        passage = {'id': 'theorem-1', 'version_id': self.versions[0], 'anchor': build_theorem(1)[:40], 'characters': 90}
        asking = self.build_reading_response(packet, excerpts=[], passages=[passage])
        openness_reading.validate_turn_output(packet, asking)
        openness_reading.validate_turn_output(packet, self.build_native_result(packet))
        exhausted = json.loads(json.dumps(packet))
        exhausted['reading_record']['allowances']['passages']['used'] = 100000
        reading = self.read_packet('turn-1')
        for checked, output in ((packet, self.build_reading_response(packet, excerpts=[])),
                                (exhausted, asking),
                                (reading, self.build_native_result(packet))):
            with self.assertRaises(Exception) as raised:
                openness_reading.validate_turn_output(checked, output)
            self.assertEqual(raised.exception.code, 'invalid_reading_response')

    def test_problem_openness_records_the_reading_turns_and_rechecks_them(self):
        from research_harness.problem_openness import assess_openness
        self.limit_input()
        final = self.run_reading_turns(self.export(priority=self.versions))[-1]
        self.assert_error('openness_reading_incomplete', lambda: self.record_openness('turn-2'))
        self.invoke(final, self.build_native_result(self.read_packet(final)))
        saved = self.record_openness(final)
        self.assertEqual(saved['reading_turns'], [
            {'assignment_id': 'turn-1', 'attempt_id': 'attempt-turn-1', 'delivered': self.versions[:2]},
            {'assignment_id': 'turn-2', 'attempt_id': 'attempt-turn-2', 'delivered': self.versions[2:]}])
        self.assertEqual(saved['outcome'], 'supported_open')
        self.assertEqual(assess_openness(self.store.snapshot()['records'], self.artifacts), [])
        self.mutate(protocol.record_context_event, {'id': 'exposure', 'assignment_id': 'turn-1',
            'kind': 'contamination', 'source': 'author_history', 'reason': 'The first reading turn saw an author note.',
            'evidence': [self.texts[self.versions[0]]]})
        [stale] = assess_openness(self.store.snapshot()['records'], self.artifacts)
        self.assertEqual(stale['code'], 'problem_openness_stale')

    def test_a_chain_that_missed_a_text_is_refused(self):
        self.limit_input()
        native_packet = self.export(priority=self.versions)
        real = openness_reading.list_reading_texts

        def drop_last(records, native):
            return real(records, native)[:-1]
        with patch.object(openness_reading, 'list_reading_texts', side_effect=drop_last):
            final = self.run_reading_turns(native_packet)[-1]
        self.assertEqual(final, 'turn-2')
        self.invoke(final, self.build_native_result(self.read_packet(final)))
        with self.assertRaises(Exception) as raised:
            self.record_openness(final)
        self.assertEqual((raised.exception.code, raised.exception.details['version_ids']),
                         ('openness_reading_incomplete', [self.versions[2]]))

    def test_a_packet_that_fits_stays_single_turn_and_records_as_before(self):
        native_packet = self.export()
        assignment = self.assign('turn-1', native_packet)
        self.assertNotIn('reading_chain', assignment)
        self.assertEqual(assignment['prompt'], protocol.canonical_prompt('native_math'))
        packet = self.read_packet('turn-1')
        self.assertEqual(sorted(packet),
                         ['digest', 'dossier_id', 'evidence', 'native', 'protocol', 'reviewer_id', 'role'])
        self.invoke('turn-1', self.build_native_result(packet))
        saved = self.record_openness('turn-1')
        self.assertNotIn('reading_turns', saved)
        self.assert_error('review_source_continuation_mismatch',
                          lambda: self.assign('turn-2', native_packet, prior='turn-1'))

    def test_a_final_turn_that_does_not_fit_names_the_text_priority(self):
        self.limit_input()
        native_packet = self.export()
        self.assign('turn-1', native_packet)
        self.invoke('turn-1', self.build_reading_response(self.read_packet('turn-1')))
        self.assign('turn-2', native_packet, prior='turn-1')
        self.invoke('turn-2', self.build_reading_response(self.read_packet('turn-2')))
        with self.assertRaises(Exception) as raised:
            self.assign('turn-3', native_packet, prior='turn-2')
        self.assertEqual(raised.exception.code, 'review_input_too_large')
        self.assertIn('minimum.text_priority', str(raised.exception))

    def test_a_text_that_does_not_fit_alone_fails_the_assignment(self):
        self.limit_input(9000)
        with self.assertRaises(Exception) as raised:
            self.assign('turn-1', self.export())
        self.assertEqual(raised.exception.code, 'review_input_too_large')
        self.assertEqual(raised.exception.details['version_id'], self.versions[0])
        self.assertEqual(raised.exception.details['characters'], self.texts[self.versions[0]]['size'])
        self.assertNotIn('turn-1', self.store.snapshot()['records'].get('review_assignment', {}))

    def test_a_chain_turn_continues_the_same_reviewer_route_and_packet_without_forking(self):
        self.limit_input()
        native_packet = self.export()
        self.assign('turn-1', native_packet)
        self.invoke('turn-1', self.build_reading_response(self.read_packet('turn-1')))
        other = self.export(name='other-packet', assignment_id='other-turn')
        self.assert_error('review_source_continuation_mismatch',
                          lambda: self.assign('other-turn', other, prior='turn-1'))
        self.assign('turn-2', native_packet, prior='turn-1')
        self.assert_error('review_source_continuation_fork',
                          lambda: self.assign('turn-2b', native_packet, prior='turn-1'))
        records = self.store.snapshot()['records']
        self.assertEqual(records['review_assignment']['turn-2']['context'],
                         {'native_packet': native_packet, 'prior_assignment_id': 'turn-1'})
        self.assertTrue(protocol._prompt_matches(records['review_assignment']['turn-2']))
        changed = dict(records['review_assignment']['turn-2'])
        changed['prompt'] = dict(changed['prompt'], text=changed['prompt']['text'] + ' ')
        self.assertFalse(protocol._prompt_matches(changed))
